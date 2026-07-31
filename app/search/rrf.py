from collections import defaultdict

from app.search.search_result import SearchResult


class ReciprocalRankFusion:
    """Reciprocal Rank Fusion over any number of rankings.

    Each ranking contributes 1/(k + rank) to a document's score, so a result that
    appears reasonably high in several rankings outranks one that tops a single
    ranking. Scores are comparable across retrievers without normalisation, which
    is the reason to use RRF here rather than blending raw BM25 and cosine values.
    """

    def __init__(self, k: int = 60) -> None:
        self.k = k

    def fuse(self, *rankings: list[SearchResult]) -> list[SearchResult]:

        scores: dict[int, float] = defaultdict(float)

        documents: dict[int, SearchResult] = {}

        for ranking in rankings:
            for rank, result in enumerate(ranking, start=1):

                documents.setdefault(result.id, result)

                scores[result.id] += 1 / (self.k + rank)

        ordered = sorted(
            documents.values(),
            key=lambda result: scores[result.id],
            reverse=True,
        )

        for result in ordered:
            result.score = scores[result.id]

        return ordered
