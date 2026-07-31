from dataclasses import dataclass


@dataclass(slots=True)
class SearchConfig:
    """Retrieval settings.

    These are the knobs the retrieval evaluation will sweep, which is why they
    live in a dataclass that can be constructed per experiment rather than being
    read from the environment.
    """

    keyword_limit: int = 30

    vector_limit: int = 30

    # Candidates handed to the cross-encoder.
    rerank_limit: int = 20

    # Results returned to the caller.
    final_limit: int = 5

    rrf_k: int = 60

    enable_keyword_search: bool = True

    enable_vector_search: bool = True

    enable_query_expansion: bool = True

    enable_reranking: bool = True

    max_workers: int = 8
