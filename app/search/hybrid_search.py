"""Hybrid retrieval: BM25 + dense vectors, fused with RRF, then reranked."""

from concurrent.futures import ThreadPoolExecutor
import logging
import time

from app.search.keyword_search import KeywordSearch
from app.search.query_expander import QueryExpander
from app.search.reranker import get_reranker
from app.search.rrf import ReciprocalRankFusion
from app.search.search_config import SearchConfig
from app.search.search_result import SearchResult
from app.search.vector_search import VectorSearch

log = logging.getLogger(__name__)


class HybridSearch:

    def __init__(self, config: SearchConfig | None = None) -> None:

        self.config = config or SearchConfig()

        self.keyword = KeywordSearch()
        self.vector = VectorSearch()
        self.expander = QueryExpander()

        self.rrf = ReciprocalRankFusion(self.config.rrf_k)

        # Loaded lazily so a keyword-only or vector-only configuration does not
        # pay for the cross-encoder.
        self._reranker = None

    # ---------------------------------------------------------

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = get_reranker()

        return self._reranker

    # ---------------------------------------------------------

    def search(self, query: str) -> list[SearchResult]:

        started = time.perf_counter()

        queries = [query]

        if self.config.enable_query_expansion:
            queries = self.expander.expand(query)

        rankings = self.retrieve(queries)

        # Each (query, retriever) pair is fused as its own ranking. Concatenating
        # them into two big lists first — as the previous version did — destroys
        # the rank positions that RRF is built on.
        results = self.rrf.fuse(*rankings)

        for result in results:
            result.source = "rrf"

        if self.config.enable_reranking:
            # Exactly one rerank pass. The previous version reranked
            # unconditionally and then again when enabled, doubling the cost of
            # the slowest stage in the pipeline.
            results = self.reranker.rerank(
                query,
                results[: self.config.rerank_limit],
            )

        final = results[: self.config.final_limit]

        log.debug(
            "Search %r: %s expansions, %s fused, %s returned in %.3fs",
            query,
            len(queries),
            len(results),
            len(final),
            time.perf_counter() - started,
        )

        return final

    # ---------------------------------------------------------

    def retrieve(self, queries: list[str]) -> list[list[SearchResult]]:
        """Run every retriever against every query, concurrently."""

        rankings: list[list[SearchResult]] = []

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:

            # All futures are submitted before any result is collected; the
            # previous version blocked on each result inside the submit loop,
            # which serialised the expanded queries.
            futures = []

            for expanded in queries:

                if self.config.enable_keyword_search:
                    futures.append(
                        executor.submit(
                            self.keyword.search,
                            expanded,
                            self.config.keyword_limit,
                        )
                    )

                if self.config.enable_vector_search:
                    futures.append(
                        executor.submit(
                            self.vector.search,
                            expanded,
                            self.config.vector_limit,
                        )
                    )

            for future in futures:
                try:
                    ranking = future.result()

                except Exception:
                    log.exception("A retriever failed; continuing without it")
                    continue

                if ranking:
                    rankings.append(ranking)

        return rankings
