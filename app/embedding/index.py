"""Embed the chunks table into Qdrant.

The run is resumable. `chunks.embedding_model` records the backend and model that
embedded each row, and a non-rebuild run picks up every row whose marker does not
match the current configuration. That gives two things at once: an interrupted
run continues instead of restarting, and changing the model or backend
automatically re-embeds — which matters because two backends do not share a
vector space, and a half-migrated collection would silently return nonsense.
"""

from dataclasses import dataclass
import logging
import time

from sqlalchemy import func, select, update
from tqdm import tqdm

from app.config import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_MAX_TOKENS,
    EMBEDDING_SIGNATURE,
)
from app.db.connection import get_session
from app.db.schema import chunks as chunks_table
from app.db.schema import documents as documents_table
from app.embedding.embedder import get_embedder
from app.embedding.qdrant_client import get_repository

log = logging.getLogger(__name__)


@dataclass
class IndexStats:
    embedded: int = 0
    skipped: int = 0
    truncated: int = 0
    seconds: float = 0.0

    @property
    def rate(self) -> float:
        return self.embedded / self.seconds if self.seconds else 0.0


class EmbeddingIndexer:

    def __init__(self, rebuild: bool = False) -> None:

        self.rebuild = rebuild
        self.stats = IndexStats()

        self.embedder = get_embedder()
        self.repository = get_repository()

    # ---------------------------------------------------------

    def run(self) -> IndexStats:

        session = get_session()
        started = time.perf_counter()

        try:
            self.repository.create_collection(
                self.embedder.dimension,
                force=self.rebuild,
            )

            if self.rebuild:
                # Clear the resume markers so every chunk is re-embedded.
                session.execute(
                    update(chunks_table).values(embedding_model=None)
                )
                session.commit()

            pending, already_done = self.pending_counts(session)

            if not pending:
                log.info("Nothing to embed; all %s chunks are indexed", f"{already_done:,}")
                self.stats.skipped = already_done
                return self.stats

            if already_done:
                log.info(
                    "Resuming: %s chunks already indexed, %s remaining",
                    f"{already_done:,}",
                    f"{pending:,}",
                )

            self.stats.skipped = already_done

            self.repository.begin_bulk_load()

            try:
                self.embed_pending(session, pending)

            finally:
                self.repository.end_bulk_load(
                    expected=already_done + self.stats.embedded
                )

            self.stats.seconds = time.perf_counter() - started

            self.log_summary()

        finally:
            session.close()

        return self.stats

    # ---------------------------------------------------------

    @staticmethod
    def _pending_filter():
        """Rows that still need embedding under the current configuration."""

        return (chunks_table.c.embedding_model.is_(None)) | (
            chunks_table.c.embedding_model != EMBEDDING_SIGNATURE
        )

    def pending_counts(self, session) -> tuple[int, int]:
        """Return (chunks still to embed, chunks already embedded)."""

        pending = session.execute(
            select(func.count())
            .select_from(chunks_table)
            .where(self._pending_filter())
        ).scalar_one()

        total = session.execute(
            select(func.count()).select_from(chunks_table)
        ).scalar_one()

        return pending, total - pending

    # ---------------------------------------------------------

    def embed_pending(self, session, pending: int) -> None:

        progress = tqdm(total=pending, desc="Embedding", unit="chunk")

        try:
            while True:
                rows = self.fetch_batch(session)

                if not rows:
                    break

                self.process_batch(session, rows)

                progress.update(len(rows))

        finally:
            progress.close()

    def fetch_batch(self, session) -> list:
        """Fetch the next batch of unembedded chunks with their document metadata."""

        statement = (
            select(
                chunks_table.c.id,
                chunks_table.c.document_id,
                chunks_table.c.chunk_index,
                chunks_table.c.header_path,
                chunks_table.c.content,
                chunks_table.c.token_count,
                documents_table.c.title,
                documents_table.c.url,
                documents_table.c.category,
            )
            .join(
                documents_table,
                chunks_table.c.document_id == documents_table.c.id,
            )
            .where(self._pending_filter())
            .order_by(chunks_table.c.id)
            .limit(EMBEDDING_BATCH_SIZE)
        )

        return session.execute(statement).all()

    def process_batch(self, session, rows: list) -> None:

        texts = [row.content for row in rows]

        vectors = self.embedder.embed(texts)

        payloads = [
            {
                "chunk_id": row.id,
                "document_id": row.document_id,
                "chunk_index": row.chunk_index,
                "header_path": row.header_path,
                "title": row.title,
                "url": row.url,
                "category": row.category,
                "content": row.content,
            }
            for row in rows
        ]

        self.repository.upsert(
            ids=[row.id for row in rows],
            vectors=vectors,
            payloads=payloads,
        )

        # Mark as done only after the upsert returns, so a crash mid-batch leaves
        # those rows pending rather than silently missing from the index.
        session.execute(
            update(chunks_table)
            .where(chunks_table.c.id.in_([row.id for row in rows]))
            .values(embedding_model=EMBEDDING_SIGNATURE)
        )
        session.commit()

        self.stats.embedded += len(rows)

        self.stats.truncated += sum(
            1 for row in rows if row.token_count > EMBEDDING_MAX_TOKENS
        )

    # ---------------------------------------------------------

    def log_summary(self) -> None:

        log.info("Embedding complete")
        log.info("  chunks embedded : %s", f"{self.stats.embedded:,}")
        log.info(
            "  elapsed         : %.1fs (%.0f chunks/s)",
            self.stats.seconds,
            self.stats.rate,
        )
        log.info("  vectors in qdrant: %s", f"{self.repository.count():,}")

        if self.stats.truncated:
            log.warning(
                "  %s chunks exceed the model limit of %s tokens and were "
                "truncated by the encoder; consider lowering CHUNK_MAX_TOKENS",
                f"{self.stats.truncated:,}",
                EMBEDDING_MAX_TOKENS,
            )
