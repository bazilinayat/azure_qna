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

    # Both retrievers on: fusing them measured decisively better than either
    # alone (MRR 0.835 vs 0.765 keyword-only and 0.767 vector-only over 450
    # ground truth questions). See EVALUATION.md.
    enable_keyword_search: bool = True

    enable_vector_search: bool = True

    # Both default OFF because the evaluation found no measurable benefit.
    #
    # Query expansion was slightly negative on every metric. Reranking bought
    # +0.7pp hit rate -- inside the ~1.2pp standard error at n=450 -- while
    # lowering MRR by 1.0pp, lowering hit@1 by 2.2pp, and costing 5.7x the
    # latency (1.91s vs 0.33s per query).
    #
    # Both are implemented, tested and evaluated; they are simply not on by
    # default because the numbers do not justify the cost. Flip either to True
    # to re-enable, or pass --no-expand / --no-rerank to invert from the CLI.
    enable_query_expansion: bool = False

    enable_reranking: bool = False

    max_workers: int = 8
