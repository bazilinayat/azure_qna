"""Qdrant access layer."""

from functools import lru_cache
import logging
import time

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Batch,
    Distance,
    OptimizersConfigDiff,
    PayloadSchemaType,
    VectorParams,
)

from app.config import (
    QDRANT_COLLECTION,
    QDRANT_GRPC_PORT,
    QDRANT_HOST,
    QDRANT_INDEXING_THRESHOLD,
    QDRANT_ON_DISK_PAYLOAD,
    QDRANT_PORT,
    QDRANT_PREFER_GRPC,
)

log = logging.getLogger(__name__)


class QdrantRepository:

    def __init__(self) -> None:

        self.client = QdrantClient(
            host=QDRANT_HOST,
            port=QDRANT_PORT,
            grpc_port=QDRANT_GRPC_PORT,
            prefer_grpc=QDRANT_PREFER_GRPC,
            timeout=120,
        )

        self.collection = QDRANT_COLLECTION

    # ---------------------------------------------------------
    # Collection lifecycle
    # ---------------------------------------------------------

    def collection_exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def create_collection(self, vector_size: int, force: bool = False) -> bool:
        """Create the collection. Returns True if it was (re)created.

        With `force`, an existing collection is deleted first — `recreate_collection`
        is deprecated in the current client, so this is the supported equivalent.
        """

        if self.collection_exists():

            if not force:
                log.info("Collection '%s' already exists", self.collection)
                return False

            log.info("Deleting existing collection '%s'", self.collection)
            self.client.delete_collection(self.collection)

        log.info(
            "Creating collection '%s' (dim=%s, on_disk_payload=%s)",
            self.collection,
            vector_size,
            QDRANT_ON_DISK_PAYLOAD,
        )

        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(
                size=vector_size,
                distance=Distance.COSINE,
            ),
            # Payloads carry the full chunk text. Keeping them on disk holds RAM
            # down to the vectors alone, which is what makes this deployable on a
            # small cloud VM later.
            on_disk_payload=QDRANT_ON_DISK_PAYLOAD,
        )

        self.create_payload_indexes()

        return True

    def create_payload_indexes(self) -> None:
        """Index the payload fields used for filtering."""

        for field, schema in (
            ("category", PayloadSchemaType.KEYWORD),
            ("document_id", PayloadSchemaType.INTEGER),
        ):
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=schema,
            )

    # ---------------------------------------------------------
    # Bulk load tuning
    # ---------------------------------------------------------

    def begin_bulk_load(self) -> None:
        """Suspend HNSW index building for the duration of a bulk load.

        Qdrant's documented bulk-upload recipe. Building the graph incrementally
        while tens of thousands of points stream in is far slower than loading
        first and building the index once at the end.
        """

        log.info("Suspending HNSW indexing for bulk load")

        self.client.update_collection(
            collection_name=self.collection,
            optimizers_config=OptimizersConfigDiff(indexing_threshold=0),
        )

    def end_bulk_load(self, expected: int | None = None) -> None:
        """Re-enable indexing so the HNSW graph gets built.

        Upserts are issued with wait=False for throughput, so the point count is
        eventually consistent. If an expected total is given, this waits briefly
        for it to settle — otherwise the run summary reports a count lower than
        the number of chunks actually sent, which looks like data loss.
        """

        if expected is not None:
            self._await_count(expected)

        log.info(
            "Restoring HNSW indexing (threshold=%s)",
            QDRANT_INDEXING_THRESHOLD,
        )

        self.client.update_collection(
            collection_name=self.collection,
            optimizers_config=OptimizersConfigDiff(
                indexing_threshold=QDRANT_INDEXING_THRESHOLD
            ),
        )

    def _await_count(self, expected: int, timeout: float = 60.0) -> None:
        """Poll until the collection reports `expected` points, or give up."""

        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            current = self.count()

            if current >= expected:
                return

            time.sleep(0.5)

        log.warning(
            "Qdrant reported %s of %s points after %.0fs; "
            "the remaining upserts may still be in flight",
            f"{self.count():,}",
            f"{expected:,}",
            timeout,
        )

    # ---------------------------------------------------------
    # Writes
    # ---------------------------------------------------------

    def upsert(
        self,
        ids: list[int],
        vectors: list[list[float]],
        payloads: list[dict],
    ) -> None:
        """Upsert a batch of points.

        `Batch` passes three parallel lists straight through to serialisation.
        Building one `PointStruct` per point instead means running pydantic
        validation over every 384-float vector, which is measurable overhead at
        this scale.
        """

        self.client.upsert(
            collection_name=self.collection,
            points=Batch(ids=ids, vectors=vectors, payloads=payloads),
            wait=False,
        )

    # ---------------------------------------------------------
    # Reads
    # ---------------------------------------------------------

    def search(self, vector: list[float], limit: int = 20):
        """Nearest-neighbour search. Returns a list of scored points."""

        response = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            with_payload=True,
        )

        return response.points

    def count(self) -> int:
        if not self.collection_exists():
            return 0

        return self.client.count(collection_name=self.collection).count

    def info(self) -> dict:
        """Summary used by the pipeline's verification stage."""

        if not self.collection_exists():
            return {"exists": False}

        info = self.client.get_collection(self.collection)

        return {
            "exists": True,
            "points": info.points_count,
            "indexed_vectors": info.indexed_vectors_count,
            "status": str(info.status),
        }


@lru_cache(maxsize=1)
def get_repository() -> QdrantRepository:
    """Return the process-wide Qdrant repository."""

    return QdrantRepository()
