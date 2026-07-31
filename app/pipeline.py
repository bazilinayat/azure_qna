"""One entry point that runs the whole data pipeline serially.

    uv run python -m app.pipeline --fresh

Stages run in dependency order and each one logs its own timing and row counts.
Everything goes to the console and to a timestamped file under logs/.

    --fresh              drop the database, re-ingest, re-index, rebuild vectors
    --chunk-size 256     build with a different chunk size (own db + collection)
    --stages ingest,fts  run only the named stages
    --from embed         run from the named stage to the end
    --skip verify        run everything except the named stages

Note on imports: nothing from `app.*` is imported at module level. Settings in
app/config.py are evaluated once at import time, and --chunk-size works by
setting the corresponding environment variable before that happens. Importing
config up here would freeze the defaults before the flag could be applied.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable
import argparse
import logging
import os
import sys
import time

log = logging.getLogger(__name__)

STAGE_ORDER = ["database", "ingest", "fts", "embed", "verify"]


@dataclass
class StageResult:
    name: str
    seconds: float
    details: dict = field(default_factory=dict)
    failed: bool = False


# --------------------------------------------------
# Stage options
# --------------------------------------------------

@dataclass
class PipelineOptions:
    reset_database: bool = False
    refresh_source: bool = False
    all_categories: bool = False
    rebuild_vectors: bool = False
    limit: int | None = None


# --------------------------------------------------
# Stages
# --------------------------------------------------

def stage_database(options: PipelineOptions) -> dict:
    """Create (or reset) the schema and the FTS5 table."""

    from app.db.db_init import create_database

    create_database(reset=options.reset_database)

    return {"reset": options.reset_database}


def stage_ingest(options: PipelineOptions) -> dict:
    """Clone the docs repositories and load documents + chunks into SQLite."""

    from app.ingest.ingest import AzureDocsIngestor

    stats = AzureDocsIngestor(
        refresh=options.refresh_source,
        all_categories=options.all_categories,
        limit=options.limit,
    ).run()

    return {
        "documents": stats.documents,
        "chunks": stats.chunks,
        "categories": len(stats.categories),
        "failed": stats.failed,
    }


def stage_fts(options: PipelineOptions) -> dict:
    """Rebuild the BM25 index from the current chunks table."""

    from app.db import fts
    from app.db.connection import get_engine

    with get_engine().begin() as connection:
        indexed = fts.rebuild(connection)

    log.info("FTS index rebuilt with %s rows", f"{indexed:,}")

    return {"indexed": indexed}


def stage_embed(options: PipelineOptions) -> dict:
    """Embed chunks and upsert them into Qdrant."""

    from app.embedding.index import EmbeddingIndexer

    stats = EmbeddingIndexer(rebuild=options.rebuild_vectors).run()

    return {
        "embedded": stats.embedded,
        "already_indexed": stats.skipped,
        "chunks_per_second": round(stats.rate, 1),
    }


def stage_verify(options: PipelineOptions) -> dict:
    """End-to-end sanity check: counts line up and a real query returns hits."""

    from sqlalchemy import func, select

    from app.config import EMBEDDING_SIGNATURE
    from app.db import fts
    from app.db.connection import get_engine, get_session
    from app.db.schema import chunks as chunks_table
    from app.db.schema import documents as documents_table
    from app.embedding.qdrant_client import get_repository

    details: dict = {}

    with get_session() as session:
        details["documents"] = session.execute(
            select(func.count()).select_from(documents_table)
        ).scalar_one()

        details["chunks"] = session.execute(
            select(func.count()).select_from(chunks_table)
        ).scalar_one()

        # Counts rows embedded by the *current* model/backend, so a stale
        # half-migrated index is reported as a problem rather than as complete.
        details["embedded"] = session.execute(
            select(func.count())
            .select_from(chunks_table)
            .where(chunks_table.c.embedding_model == EMBEDDING_SIGNATURE)
        ).scalar_one()

    with get_engine().connect() as connection:
        details["fts_rows"] = fts.count(connection) if fts.exists(connection) else 0

    details["qdrant"] = get_repository().info()

    problems = []

    if details["chunks"] != details["fts_rows"]:
        problems.append(
            f"FTS index has {details['fts_rows']:,} rows but there are "
            f"{details['chunks']:,} chunks; rerun the fts stage"
        )

    if details["chunks"] != details["embedded"]:
        problems.append(
            f"{details['chunks'] - details['embedded']:,} chunks are not embedded; "
            "rerun the embed stage"
        )

    # A live query exercises both retrievers, the fusion and the reranker.
    from app.search.hybrid_search import HybridSearch

    probe = "How do I make a blob container publicly accessible?"

    results = HybridSearch().search(probe)

    details["probe_query"] = probe
    details["probe_results"] = len(results)

    if results:
        details["probe_top_hit"] = results[0].title
        log.info("Probe query returned %s results", len(results))
        log.info("  top hit: %s", results[0].title)
        log.info("  %s", results[0].url)
    else:
        problems.append("The probe query returned no results")

    for problem in problems:
        log.warning("Verification: %s", problem)

    details["problems"] = problems

    return details


STAGES: dict[str, Callable[[PipelineOptions], dict]] = {
    "database": stage_database,
    "ingest": stage_ingest,
    "fts": stage_fts,
    "embed": stage_embed,
    "verify": stage_verify,
}


# --------------------------------------------------
# Runner
# --------------------------------------------------

def resolve_stages(args: argparse.Namespace) -> list[str]:
    """Work out which stages to run from the CLI flags."""

    if args.stages:
        requested = [name.strip() for name in args.stages.split(",") if name.strip()]

        unknown = [name for name in requested if name not in STAGES]

        if unknown:
            raise SystemExit(
                f"Unknown stage(s): {', '.join(unknown)}. "
                f"Valid stages: {', '.join(STAGE_ORDER)}"
            )

        # Always run in dependency order regardless of the order given.
        selected = [name for name in STAGE_ORDER if name in requested]

    elif args.from_stage:
        if args.from_stage not in STAGES:
            raise SystemExit(
                f"Unknown stage: {args.from_stage}. "
                f"Valid stages: {', '.join(STAGE_ORDER)}"
            )

        start = STAGE_ORDER.index(args.from_stage)
        selected = STAGE_ORDER[start:]

    else:
        selected = list(STAGE_ORDER)

    if args.skip:
        skipped = {name.strip() for name in args.skip.split(",") if name.strip()}
        selected = [name for name in selected if name not in skipped]

    return selected


def log_configuration(options: PipelineOptions, stages: list[str]) -> None:

    from app.config import (
        CHUNK_MAX_TOKENS,
        CHUNK_MIN_TOKENS,
        CHUNK_OVERLAP_TOKENS,
        COLLECTION_IS_PINNED,
        DATABASE_PATH,
        EMBEDDING_BACKEND,
        EMBEDDING_MODEL,
        EMBEDDING_THREADS,
        QDRANT_COLLECTION,
        QDRANT_HOST,
        QDRANT_PORT,
        STORAGE_PATH_IS_PINNED,
    )

    log.info("AzureMentor pipeline")
    log.info("  stages          : %s", " -> ".join(stages))
    log.info("  embedding model : %s", EMBEDDING_MODEL)
    log.info("  backend         : %s (%s threads)", EMBEDDING_BACKEND, EMBEDDING_THREADS)
    log.info(
        "  chunking        : max %s, overlap %s, min %s tokens",
        CHUNK_MAX_TOKENS,
        CHUNK_OVERLAP_TOKENS,
        CHUNK_MIN_TOKENS,
    )
    log.info("  database        : %s", DATABASE_PATH)
    log.info("  qdrant          : %s:%s/%s", QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION)
    log.info(
        "  scope           : %s",
        "all categories" if options.all_categories else "curated categories",
    )

    if options.limit:
        log.info("  limit           : %s files per repo", options.limit)

    # Silent collision between chunk profiles is the worst failure mode here:
    # the run succeeds, the numbers look plausible, and the comparison is void.
    if STORAGE_PATH_IS_PINNED:
        log.warning(
            "DATABASE_PATH is pinned in .env, so every chunk size writes to "
            "%s. Remove that line to give each chunk size its own database.",
            DATABASE_PATH.name,
        )

    if COLLECTION_IS_PINNED:
        log.warning(
            "QDRANT_COLLECTION is pinned in .env, so every chunk size writes to "
            "'%s'. Remove that line to give each chunk size its own collection.",
            QDRANT_COLLECTION,
        )


def log_summary(results: list[StageResult], total: float, log_path) -> None:

    log.info("")
    log.info("=" * 66)
    log.info("Pipeline summary")
    log.info("=" * 66)

    for result in results:
        status = "FAILED" if result.failed else "ok"

        log.info(
            "  %-10s %8s  %s",
            result.name,
            str(timedelta(seconds=int(result.seconds))),
            status,
        )

        for key, value in result.details.items():
            if key == "problems":
                continue

            log.info("      %-18s %s", key, value)

    log.info("-" * 66)
    log.info("  total      %8s", str(timedelta(seconds=int(total))))

    if log_path:
        log.info("  log file   %s", log_path)


def run(options: PipelineOptions, stages: list[str], log_path) -> int:

    log_configuration(options, stages)

    results: list[StageResult] = []
    started = time.perf_counter()

    for name in stages:

        log.info("")
        log.info("-" * 66)
        log.info("Stage: %s", name)
        log.info("-" * 66)

        stage_started = time.perf_counter()

        try:
            details = STAGES[name](options) or {}

        except Exception:
            elapsed = time.perf_counter() - stage_started

            log.exception("Stage '%s' failed after %.1fs", name, elapsed)

            results.append(
                StageResult(name=name, seconds=elapsed, failed=True)
            )

            log_summary(results, time.perf_counter() - started, log_path)

            return 1

        elapsed = time.perf_counter() - stage_started

        log.info("Stage '%s' finished in %.1fs", name, elapsed)

        results.append(
            StageResult(name=name, seconds=elapsed, details=details)
        )

    total = time.perf_counter() - started

    log_summary(results, total, log_path)

    # A verification stage that found problems is a soft failure: the data is
    # loaded but inconsistent, and the exit code needs to say so.
    for result in results:
        if result.details.get("problems"):
            return 2

    return 0


def main() -> None:

    parser = argparse.ArgumentParser(
        description="Run the AzureMentor data pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Full rebuild: reset the database and re-embed everything.",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        metavar="TOKENS",
        help=(
            "Chunk size in embedding-model tokens. Overlap, minimum chunk size, "
            "the database file and the Qdrant collection all derive from it, so "
            "each size builds its own index and they never overwrite each other."
        ),
    )

    parser.add_argument(
        "--stages",
        help=f"Comma-separated stages to run. Valid: {', '.join(STAGE_ORDER)}",
    )

    parser.add_argument(
        "--from",
        dest="from_stage",
        help="Run from this stage to the end.",
    )

    parser.add_argument(
        "--skip",
        help="Comma-separated stages to skip.",
    )

    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="Drop and recreate the tables.",
    )

    parser.add_argument(
        "--refresh-source",
        action="store_true",
        help="Pull the latest upstream docs before ingesting.",
    )

    parser.add_argument(
        "--rebuild-vectors",
        action="store_true",
        help="Recreate the Qdrant collection and re-embed every chunk.",
    )

    parser.add_argument(
        "--all-categories",
        action="store_true",
        help="Ingest all 145 service folders instead of the curated set.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="Ingest at most N files per repository. Useful for a smoke test.",
    )

    args = parser.parse_args()

    # Must happen before the first `app.config` import, because config evaluates
    # its settings once at import time and everything else derives from them.
    if args.chunk_size is not None:
        os.environ["CHUNK_MAX_TOKENS"] = str(args.chunk_size)

    # Stages are resolved before importing anything, so a typo in --stages fails
    # instantly rather than after loading torch.
    stages = resolve_stages(args)

    from app.config import INGEST_ALL_CATEGORIES
    from app.logging_setup import setup_logging

    log_path = setup_logging("pipeline")

    options = PipelineOptions(
        reset_database=args.reset_db or args.fresh,
        refresh_source=args.refresh_source,
        all_categories=args.all_categories or INGEST_ALL_CATEGORIES,
        rebuild_vectors=args.rebuild_vectors or args.fresh,
        limit=args.limit,
    )

    sys.exit(run(options, stages, log_path))


if __name__ == "__main__":
    main()
