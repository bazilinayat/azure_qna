"""Embedding backends.

Two backends, same interface, selected by EMBEDDING_BACKEND.

Why there are two: fastembed ships the *int8-quantized* ONNX build of
bge-small-en-v1.5, and on this hardware it is roughly four times slower than the
plain fp32 torch model on identical input — measured 2.2 chunks/s versus 8.3
chunks/s over 128 real chunks averaging 430 tokens. Quantized transformers only
win on CPUs whose kernels are optimised for the relevant int8 op set; otherwise
dynamic quantization of the attention matmuls costs more than it saves. So torch
is the default and fastembed stays available for comparison.

Throughput turned out to be near-constant in characters per second (3,555 for
fastembed, 13,574 for torch), so it is linear in total corpus text: changing the
chunk size moves the number of chunks but not the total work.

Threading scales only to about 8 threads here (5.2 / 8.1 / 8.3 chunks/s at 4 / 8
/ 16 threads), and a multi-process pool reached just 10.3 chunks/s — a 20% gain
for a large amount of complexity, so it is deliberately not implemented.

IMPORTANT: the two backends do not produce bit-identical vectors. Switching
backends requires re-embedding, which happens automatically because the resume
marker records the backend (see EMBEDDING_SIGNATURE).
"""

from functools import lru_cache
import logging

from app.config import (
    EMBEDDING_BACKEND,
    EMBEDDING_MAX_TOKENS,
    EMBEDDING_MODEL,
    EMBEDDING_MODEL_BATCH_SIZE,
    EMBEDDING_QUERY_PREFIX,
    EMBEDDING_THREADS,
)

log = logging.getLogger(__name__)


class TorchEmbedder:
    """sentence-transformers / PyTorch CPU backend. The default."""

    name = "torch"

    def __init__(self) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        log.info(
            "Loading embedding model %s (torch, %s threads)",
            EMBEDDING_MODEL,
            EMBEDDING_THREADS,
        )

        torch.set_num_threads(EMBEDDING_THREADS)

        self.model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
        self.model.max_seq_length = EMBEDDING_MAX_TOKENS

        # Renamed in newer sentence-transformers; the old name still works but
        # emits a FutureWarning on every startup.
        if hasattr(self.model, "get_embedding_dimension"):
            self._dimension = self.model.get_embedding_dimension()
        else:
            self._dimension = self.model.get_sentence_embedding_dimension()

        log.info("Embedding model ready (dim=%s)", self._dimension)

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors = self.model.encode(
            texts,
            batch_size=EMBEDDING_MODEL_BATCH_SIZE,
            # bge is trained for cosine similarity; the Qdrant collection uses
            # COSINE distance, so vectors must be unit-normalised.
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([_apply_query_prefix(text)])[0]


class FastEmbedEmbedder:
    """FastEmbed / quantized ONNX backend. Kept for comparison."""

    name = "fastembed"

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        log.info("Loading embedding model %s (fastembed)", EMBEDDING_MODEL)

        self.model = TextEmbedding(
            model_name=EMBEDDING_MODEL,
            threads=EMBEDDING_THREADS,
        )

        self._dimension = len(next(self.model.embed(["dimension probe"])))

        log.info("Embedding model ready (dim=%s)", self._dimension)

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # parallel=1 keeps everything in this process. Multiprocessing measured
        # no faster than single-process for this model, and on Windows the spawn
        # cost per embed() call made it dramatically worse.
        vectors = self.model.embed(
            texts,
            batch_size=EMBEDDING_MODEL_BATCH_SIZE,
            parallel=1,
        )

        return [vector.tolist() for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([_apply_query_prefix(text)])[0]


def _apply_query_prefix(text: str) -> str:
    """Prepend the bge retrieval instruction to a query.

    bge-small-en-v1.5's model card specifies an instruction prefix on the query
    side only (passages are embedded bare). It is enabled by default because it
    is the documented usage, but it is a config flag precisely so the retrieval
    evaluation can A/B it rather than take it on faith.
    """

    if not EMBEDDING_QUERY_PREFIX:
        return text

    return f"{EMBEDDING_QUERY_PREFIX}{text}"


_BACKENDS = {
    "torch": TorchEmbedder,
    "fastembed": FastEmbedEmbedder,
}


@lru_cache(maxsize=1)
def get_embedder():
    """Return the process-wide embedder, loading it on first use."""

    backend = EMBEDDING_BACKEND.strip().lower()

    if backend not in _BACKENDS:
        raise ValueError(
            f"Unknown EMBEDDING_BACKEND {EMBEDDING_BACKEND!r}. "
            f"Valid options: {', '.join(_BACKENDS)}"
        )

    return _BACKENDS[backend]()
