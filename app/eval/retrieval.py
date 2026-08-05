"""Retrieval evaluation: hit rate and MRR over the ground truth set.

A ground truth item pairs a question with the document it was generated from. A
retriever is scored on whether that document comes back, and how high.

    hit rate @k  fraction of questions whose correct document appears in the
                 top k. "Did the answer reach the LLM at all?"

    MRR          mean of 1/rank of the correct document, 0 when absent. Rewards
                 ranking it first rather than fifth, which matters because the
                 context window is small and early chunks get more attention.

Both are computed over *documents*, not chunks: several chunks of the same
article are all correct, and any of them lets the LLM answer.

No LLM calls happen here, so a sweep costs only local compute.
"""

from dataclasses import dataclass, field
import logging
import time

from tqdm import tqdm

from app.eval.ground_truth import GroundTruthItem
from app.search.search_config import SearchConfig

log = logging.getLogger(__name__)


@dataclass
class RetrievalMetrics:
    name: str
    questions: int = 0

    hit_rate: float = 0.0
    mrr: float = 0.0

    hit_rate_at_1: float = 0.0
    hit_rate_at_3: float = 0.0

    seconds_per_query: float = 0.0

    # Rank of the correct document per question; None when it was not retrieved.
    ranks: list[int | None] = field(default_factory=list)

    def as_row(self) -> dict:
        return {
            "config": self.name,
            "questions": self.questions,
            "hit_rate": round(self.hit_rate, 4),
            "mrr": round(self.mrr, 4),
            "hit@1": round(self.hit_rate_at_1, 4),
            "hit@3": round(self.hit_rate_at_3, 4),
            "sec_per_query": round(self.seconds_per_query, 3),
        }


def evaluate(
    items: list[GroundTruthItem],
    config: SearchConfig | None = None,
    name: str = "hybrid+rerank",
    search=None,
    progress: bool = True,
) -> RetrievalMetrics:
    """Score one retrieval configuration against the ground truth."""

    if search is None:
        from app.search.hybrid_search import HybridSearch

        search = HybridSearch(config or SearchConfig())

    ranks: list[int | None] = []
    started = time.perf_counter()

    iterator = tqdm(items, desc=name, unit="q") if progress else items

    for item in iterator:

        try:
            results = search.search(item.question)

        except Exception:
            log.exception("Retrieval failed for %r", item.question)
            ranks.append(None)
            continue

        rank = None

        for position, result in enumerate(results, start=1):
            if result.document_id == item.document_id:
                rank = position
                break

        ranks.append(rank)

    elapsed = time.perf_counter() - started

    return _metrics(name, ranks, elapsed)


def _metrics(name: str, ranks: list[int | None], elapsed: float) -> RetrievalMetrics:

    total = len(ranks)

    if not total:
        return RetrievalMetrics(name=name)

    found = [rank for rank in ranks if rank is not None]

    return RetrievalMetrics(
        name=name,
        questions=total,
        hit_rate=len(found) / total,
        mrr=sum(1 / rank for rank in found) / total,
        hit_rate_at_1=sum(1 for rank in found if rank == 1) / total,
        hit_rate_at_3=sum(1 for rank in found if rank <= 3) / total,
        seconds_per_query=elapsed / total,
        ranks=ranks,
    )


# --------------------------------------------------
# Sweeps
# --------------------------------------------------

def standard_configs(final_limit: int = 5) -> dict[str, SearchConfig]:
    """The comparison the course asks for: does hybrid actually beat its parts?

    Each config isolates one component, so the table shows what each stage
    contributes rather than just how good the final system is.
    """

    def base(**overrides) -> SearchConfig:
        settings = dict(
            final_limit=final_limit,
            enable_keyword_search=True,
            enable_vector_search=True,
            enable_query_expansion=False,
            enable_reranking=False,
        )
        settings.update(overrides)

        return SearchConfig(**settings)

    return {
        "keyword only": base(enable_vector_search=False),
        "vector only": base(enable_keyword_search=False),
        "hybrid (RRF)": base(),
        "hybrid + expansion": base(enable_query_expansion=True),
        "hybrid + rerank": base(enable_reranking=True),
        "hybrid + expansion + rerank": base(
            enable_query_expansion=True,
            enable_reranking=True,
        ),
    }


def sweep(
    items: list[GroundTruthItem],
    configs: dict[str, SearchConfig] | None = None,
    on_result=None,
) -> list[RetrievalMetrics]:
    """Evaluate several configurations over the same ground truth.

    A full sweep takes tens of minutes, so one configuration failing must not
    discard the ones that already succeeded. Each is isolated, and `on_result`
    fires after every configuration so the caller can persist as it goes.

    The realistic failure is memory: the cross-encoder configurations hold the
    reranker, the embedding model and Qdrant results at once, and on a machine
    also running the app container this can exhaust RAM mid-run.
    """

    from app.search.hybrid_search import HybridSearch

    configs = configs or standard_configs()

    results: list[RetrievalMetrics] = []

    for name, config in configs.items():
        log.info("Evaluating: %s", name)

        try:
            # A fresh HybridSearch per config, but the embedder, reranker and
            # Qdrant client underneath are process-wide singletons, so models
            # load once for the sweep rather than once per configuration.
            metrics = evaluate(
                items,
                config=config,
                name=name,
                search=HybridSearch(config),
            )

        except Exception as exc:
            # torch reports allocation failure as a RuntimeError, not a Python
            # MemoryError, so it has to be recognised by message.
            message = str(exc).lower()

            if isinstance(exc, MemoryError) or "not enough memory" in message:
                log.error(
                    "Ran out of memory evaluating '%s'. Free some up and rerun "
                    "just this one:\n"
                    "    docker compose --profile app down\n"
                    "    uv run python -m app.eval.main retrieval --configs '%s'\n"
                    "Results from earlier configurations are kept.",
                    name,
                    name,
                )
            else:
                log.exception("Configuration '%s' failed; continuing", name)

            continue

        results.append(metrics)

        log.info(
            "  hit rate %.3f  MRR %.3f  (%.2fs/query)",
            metrics.hit_rate,
            metrics.mrr,
            metrics.seconds_per_query,
        )

        if on_result is not None:
            on_result(results)

    return results


def format_table(results: list[RetrievalMetrics]) -> str:
    """Render sweep results as a markdown table, ready to paste into a report."""

    if not results:
        return "(no results)"

    header = (
        f"| {'Configuration':<28} | {'Hit rate':>8} | {'MRR':>6} "
        f"| {'Hit@1':>6} | {'Hit@3':>6} | {'s/query':>7} |"
    )

    divider = (
        f"|{'-' * 30}|{'-' * 10}|{'-' * 8}|{'-' * 8}|{'-' * 8}|{'-' * 9}|"
    )

    lines = [header, divider]

    best = max(result.mrr for result in results)

    for result in results:
        marker = " *" if result.mrr == best else ""

        lines.append(
            f"| {result.name + marker:<28} | {result.hit_rate:>8.3f} "
            f"| {result.mrr:>6.3f} | {result.hit_rate_at_1:>6.3f} "
            f"| {result.hit_rate_at_3:>6.3f} | {result.seconds_per_query:>7.3f} |"
        )

    return "\n".join(lines)
