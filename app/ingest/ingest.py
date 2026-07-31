"""Ingestion: clone the upstream docs repositories, then load them into SQLite.

Produces two tables: `documents` (one row per article, cleaned full text) and
`chunks` (retrievable units, see `chunker.py`).
"""

from dataclasses import dataclass, field
from pathlib import Path
import argparse
import logging
import subprocess

from sqlalchemy import delete, func, insert, select
from tqdm import tqdm

from app.config import (
    INGEST_ALL_CATEGORIES,
    INGEST_CATEGORIES,
    MIN_DOC_TOKENS,
    SKIP_INCLUDE_FILES,
    SOURCE_DIR,
    SOURCE_REPOS,
)
from app.db.connection import get_session
from app.db.schema import chunks as chunks_table
from app.db.schema import documents as documents_table
from app.ingest import markdown
from app.ingest.chunker import chunk_document
from app.ingest.tokenizer import get_token_counter

log = logging.getLogger(__name__)

# Documents committed per transaction. Keeps peak memory flat and makes progress
# durable if a long run is interrupted.
COMMIT_EVERY = 500


@dataclass
class IngestStats:
    documents: int = 0
    chunks: int = 0
    skipped_include: int = 0
    skipped_short: int = 0
    skipped_empty: int = 0
    skipped_duplicate: int = 0
    failed: int = 0
    categories: set[str] = field(default_factory=set)


class AzureDocsIngestor:

    def __init__(
        self,
        refresh: bool = False,
        all_categories: bool = INGEST_ALL_CATEGORIES,
        limit: int | None = None,
    ) -> None:

        self.refresh = refresh
        self.all_categories = all_categories
        self.limit = limit

        self.allowed = set(INGEST_CATEGORIES)
        self.counter = get_token_counter()
        self.stats = IngestStats()

        SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------
    # Repository sync
    # ---------------------------------------------------------

    def sync_repository(self, repo: dict[str, str]) -> Path:
        """Clone or refresh one upstream repository. Returns its local path."""

        repo_dir = SOURCE_DIR / repo["name"]

        if not repo_dir.exists():
            log.info("Cloning %s (shallow)", repo["url"])

            subprocess.run(
                [
                    "git", "clone",
                    "--depth", "1",
                    "--filter=blob:none",
                    "--no-checkout",
                    repo["url"],
                    str(repo_dir),
                ],
                check=True,
            )

            # Only the markdown is needed. `articles/` is ~4 GB, most of which is
            # screenshots in 336 `media/` subdirectories, so a *cone-mode*
            # checkout of `articles` would still pull all of them. Non-cone mode
            # takes gitignore-style patterns, so the checkout can be restricted
            # to .md files; combined with --filter=blob:none, git then never
            # downloads the image blobs at all.
            subprocess.run(
                [
                    "git", "-C", str(repo_dir),
                    "sparse-checkout", "set", "--no-cone",
                    f"/{repo['articles_dir']}/**/*.md",
                ],
                check=True,
            )

            subprocess.run(
                ["git", "-C", str(repo_dir), "checkout"],
                check=True,
            )

            log.info("Cloned %s", repo["name"])

        elif self.refresh:
            log.info("Refreshing %s", repo["name"])

            subprocess.run(
                [
                    "git", "-C", str(repo_dir),
                    "fetch", "--depth", "1", "origin", "HEAD",
                ],
                check=True,
            )

            subprocess.run(
                ["git", "-C", str(repo_dir), "reset", "--hard", "FETCH_HEAD"],
                check=True,
            )

            log.info("Refreshed %s", repo["name"])

        else:
            log.info("%s already present, skipping sync", repo["name"])

        return repo_dir

    # ---------------------------------------------------------
    # File discovery
    # ---------------------------------------------------------

    def markdown_files(self, repo: dict[str, str], repo_dir: Path) -> list[Path]:
        """List the markdown files to ingest from one repository."""

        articles_dir = repo_dir / repo["articles_dir"]

        if not articles_dir.exists():
            raise FileNotFoundError(
                f"Articles directory not found: {articles_dir}"
            )

        selected: list[Path] = []

        for file in sorted(articles_dir.rglob("*.md")):

            relative = file.relative_to(articles_dir)

            if SKIP_INCLUDE_FILES and "includes" in relative.parts:
                self.stats.skipped_include += 1
                continue

            category = (
                relative.parts[0]
                if len(relative.parts) > 1
                else "general"
            )

            if not self.all_categories and category not in self.allowed:
                continue

            selected.append(file)

        return selected

    # ---------------------------------------------------------
    # Ingestion
    # ---------------------------------------------------------

    def clear_database(self, session) -> None:
        log.info("Clearing existing documents and chunks")

        session.execute(delete(chunks_table))
        session.execute(delete(documents_table))
        session.commit()

    def ingest_repository(
        self,
        session,
        repo: dict[str, str],
        repo_dir: Path,
        seen_urls: set[str],
    ) -> None:

        articles_dir = repo_dir / repo["articles_dir"]

        files = self.markdown_files(repo, repo_dir)

        if self.limit:
            files = files[: self.limit]

        log.info(
            "%s: %s files selected (%s include-fragments skipped)",
            repo["name"],
            f"{len(files):,}",
            f"{self.stats.skipped_include:,}",
        )

        pending = 0

        for file in tqdm(files, desc=f"Ingesting {repo['name']}", unit="doc"):

            try:
                inserted = self.ingest_file(
                    session, repo, articles_dir, file, seen_urls
                )

            except Exception:
                self.stats.failed += 1
                log.exception("Failed to ingest %s", file)
                session.rollback()
                continue

            if inserted:
                pending += 1

            if pending >= COMMIT_EVERY:
                session.commit()
                pending = 0

        session.commit()

    def ingest_file(
        self,
        session,
        repo: dict[str, str],
        articles_dir: Path,
        file: Path,
        seen_urls: set[str],
    ) -> bool:
        """Ingest one markdown file. Returns True if a document was stored."""

        parsed = markdown.parse_file(file)

        if not parsed.content:
            self.stats.skipped_empty += 1
            return False

        if self.counter.count(parsed.content) < MIN_DOC_TOKENS:
            # Redirect pages and stubs. Too short to answer anything.
            self.stats.skipped_short += 1
            return False

        relative = file.relative_to(articles_dir)

        category = (
            relative.parts[0]
            if len(relative.parts) > 1
            else "general"
        )

        url = repo["url_base"] + relative.as_posix().removesuffix(".md")

        if url in seen_urls:
            self.stats.skipped_duplicate += 1
            return False

        seen_urls.add(url)

        document_chunks = chunk_document(parsed.title, parsed.content)

        if not document_chunks:
            self.stats.skipped_empty += 1
            return False

        document_id = session.execute(
            insert(documents_table).returning(documents_table.c.id),
            {
                "title": parsed.title,
                "description": parsed.description,
                "url": url,
                "category": category,
                "source_repo": repo["name"],
                "source_path": relative.as_posix(),
                "content": parsed.content,
                "last_updated": parsed.last_updated,
            },
        ).scalar_one()

        session.execute(
            insert(chunks_table),
            [
                {
                    "document_id": document_id,
                    "chunk_index": chunk.chunk_index,
                    "header_path": chunk.header_path,
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                    "embedding_model": None,
                }
                for chunk in document_chunks
            ],
        )

        self.stats.documents += 1
        self.stats.chunks += len(document_chunks)
        self.stats.categories.add(category)

        return True

    # ---------------------------------------------------------

    def run(self) -> IngestStats:

        repo_dirs = [
            (repo, self.sync_repository(repo))
            for repo in SOURCE_REPOS
        ]

        session = get_session()
        seen_urls: set[str] = set()

        try:
            self.clear_database(session)

            for repo, repo_dir in repo_dirs:
                self.ingest_repository(session, repo, repo_dir, seen_urls)

            self.log_summary(session)

        finally:
            session.close()

        return self.stats

    def log_summary(self, session) -> None:

        stats = self.stats

        log.info("Ingestion complete")
        log.info("  documents stored : %s", f"{stats.documents:,}")
        log.info("  chunks stored    : %s", f"{stats.chunks:,}")
        log.info("  categories       : %s", len(stats.categories))
        log.info(
            "  skipped          : %s includes, %s too short, %s empty, %s duplicate",
            f"{stats.skipped_include:,}",
            f"{stats.skipped_short:,}",
            f"{stats.skipped_empty:,}",
            f"{stats.skipped_duplicate:,}",
        )

        if stats.failed:
            log.warning("  failed           : %s", f"{stats.failed:,}")

        if not stats.chunks:
            return

        token_stats = session.execute(
            select(
                func.min(chunks_table.c.token_count),
                func.avg(chunks_table.c.token_count),
                func.max(chunks_table.c.token_count),
            )
        ).one()

        log.info(
            "  chunk tokens     : min %s, avg %.0f, max %s",
            token_stats[0],
            token_stats[1] or 0,
            token_stats[2],
        )


def main() -> None:
    from app.logging_setup import setup_logging

    setup_logging("ingest")

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Pull the latest upstream changes before ingesting.",
    )

    parser.add_argument(
        "--all-categories",
        action="store_true",
        help="Ingest every service folder instead of the curated set.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ingest at most N files per repository (for quick test runs).",
    )

    args = parser.parse_args()

    AzureDocsIngestor(
        refresh=args.refresh,
        all_categories=args.all_categories or INGEST_ALL_CATEGORIES,
        limit=args.limit,
    ).run()


if __name__ == "__main__":
    main()
