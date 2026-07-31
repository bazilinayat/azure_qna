"""Cross-encoder reranking."""

from functools import lru_cache
import logging

from app.config import RERANKER_MODEL
from app.search.search_result import SearchResult

log = logging.getLogger(__name__)


class Reranker:

    def __init__(self) -> None:
        from sentence_transformers import CrossEncoder

        log.info("Loading reranker %s", RERANKER_MODEL)

        self.model = CrossEncoder(RERANKER_MODEL)

        log.info("Reranker ready")

    def rerank(
        self,
        query: str,
        results: list[SearchResult],
    ) -> list[SearchResult]:

        if not results:
            return results

        scores = self.model.predict(
            [(query, result.content) for result in results]
        )

        for result, score in zip(results, scores):
            result.score = float(score)
            result.source = "reranker"

        return sorted(results, key=lambda item: item.score, reverse=True)


@lru_cache(maxsize=1)
def get_reranker() -> Reranker:
    """Return the process-wide reranker, loading it on first use."""

    return Reranker()
