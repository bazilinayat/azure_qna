"""Answer evaluation: generate answers over ground truth, then judge them.

Retrieval evaluation asks "did we find the right page?". This asks the question
that actually matters to a user: "given what we found, was the answer any good?"

Every prompt variant is run over the same questions and scored by the same judge,
so the comparison is like for like.

This one costs money: two API calls per question per variant. Sample size is
deliberately small by default.
"""

from dataclasses import dataclass, field
import logging
import random
import time

from tqdm import tqdm

from app.eval.ground_truth import GroundTruthItem
from app.eval.judge import (
    NON_RELEVANT,
    PARTLY_RELEVANT,
    RELEVANT,
    Judgement,
    RelevanceJudge,
)
from app.llm.prompts import PROMPTS

log = logging.getLogger(__name__)


@dataclass
class AnswerEvaluation:
    """Per-question outcome, kept so bad answers can be inspected by hand."""

    question: str
    answer: str
    expected_url: str
    judgement: Judgement
    retrieved_expected: bool
    seconds: float
    tokens: int
    cost_usd: float | None


@dataclass
class AnswerMetrics:
    name: str

    questions: int = 0

    relevant: int = 0
    partly_relevant: int = 0
    non_relevant: int = 0

    # Mean of the judge's numeric score: 1.0 / 0.5 / 0.0.
    mean_score: float = 0.0

    # How often the document the question came from was actually retrieved.
    # A low relevance score with a high value here means the *prompt* is at
    # fault; low here means retrieval is, and the prompt cannot fix it.
    grounding_rate: float = 0.0

    seconds_per_answer: float = 0.0
    tokens_per_answer: float = 0.0
    total_cost_usd: float | None = None

    evaluations: list[AnswerEvaluation] = field(default_factory=list)

    def as_row(self) -> dict:
        return {
            "config": self.name,
            "questions": self.questions,
            "relevant": self.relevant,
            "partly": self.partly_relevant,
            "non_relevant": self.non_relevant,
            "mean_score": round(self.mean_score, 4),
            "grounding_rate": round(self.grounding_rate, 4),
            "sec_per_answer": round(self.seconds_per_answer, 2),
            "tokens_per_answer": round(self.tokens_per_answer, 1),
            "total_cost_usd": self.total_cost_usd,
        }


def evaluate(
    items: list[GroundTruthItem],
    prompt_name: str,
    pipeline=None,
    judge: RelevanceJudge | None = None,
    progress: bool = True,
) -> AnswerMetrics:
    """Answer every question with one prompt variant and judge the results."""

    if pipeline is None:
        from app.llm.rag import RagPipeline

        pipeline = RagPipeline(prompt=prompt_name)

    judge = judge or RelevanceJudge()

    evaluations: list[AnswerEvaluation] = []
    started = time.perf_counter()

    iterator = tqdm(items, desc=prompt_name, unit="q") if progress else items

    for item in iterator:

        try:
            result = pipeline.answer(item.question)

        except Exception:
            log.exception("Answering failed for %r", item.question)
            continue

        try:
            judgement = judge.judge(item.question, result.answer)

        except Exception:
            log.exception("Judging failed for %r", item.question)
            continue

        evaluations.append(
            AnswerEvaluation(
                question=item.question,
                answer=result.answer,
                expected_url=item.url,
                judgement=judgement,
                retrieved_expected=any(
                    source.document_id == item.document_id
                    for source in result.sources
                ),
                seconds=result.total_seconds,
                tokens=result.total_tokens,
                cost_usd=result.cost_usd,
            )
        )

    return _metrics(prompt_name, evaluations, time.perf_counter() - started)


def _metrics(
    name: str,
    evaluations: list[AnswerEvaluation],
    elapsed: float,
) -> AnswerMetrics:

    total = len(evaluations)

    if not total:
        return AnswerMetrics(name=name)

    counts = {RELEVANT: 0, PARTLY_RELEVANT: 0, NON_RELEVANT: 0}

    for evaluation in evaluations:
        counts[evaluation.judgement.relevance] = (
            counts.get(evaluation.judgement.relevance, 0) + 1
        )

    costs = [
        evaluation.cost_usd
        for evaluation in evaluations
        if evaluation.cost_usd is not None
    ]

    return AnswerMetrics(
        name=name,
        questions=total,
        relevant=counts[RELEVANT],
        partly_relevant=counts[PARTLY_RELEVANT],
        non_relevant=counts[NON_RELEVANT],
        mean_score=sum(e.judgement.score for e in evaluations) / total,
        grounding_rate=sum(1 for e in evaluations if e.retrieved_expected) / total,
        seconds_per_answer=elapsed / total,
        tokens_per_answer=sum(e.tokens for e in evaluations) / total,
        total_cost_usd=sum(costs) if costs else None,
        evaluations=evaluations,
    )


def sweep_prompts(
    items: list[GroundTruthItem],
    prompt_names: list[str] | None = None,
    sample_size: int | None = 30,
    seed: int = 42,
) -> list[AnswerMetrics]:
    """Compare prompt variants over the same sample of questions."""

    from app.llm.rag import RagPipeline

    prompt_names = prompt_names or list(PROMPTS)

    if sample_size and sample_size < len(items):
        random.seed(seed)
        items = random.sample(items, sample_size)

    log.info(
        "Evaluating %s prompt(s) over %s questions (%s API calls)",
        len(prompt_names),
        len(items),
        len(prompt_names) * len(items) * 2,
    )

    # One judge for the whole sweep, so every variant is graded identically.
    judge = RelevanceJudge()

    results = []

    for prompt_name in prompt_names:
        log.info("Evaluating prompt: %s", prompt_name)

        metrics = evaluate(
            items,
            prompt_name,
            pipeline=RagPipeline(prompt=prompt_name),
            judge=judge,
        )

        results.append(metrics)

        log.info(
            "  mean score %.3f  (%s relevant, %s partly, %s non-relevant)",
            metrics.mean_score,
            metrics.relevant,
            metrics.partly_relevant,
            metrics.non_relevant,
        )

    return results


def format_table(results: list[AnswerMetrics]) -> str:
    if not results:
        return "(no results)"

    header = (
        f"| {'Prompt':<20} | {'Score':>6} | {'Rel':>4} | {'Part':>4} "
        f"| {'Non':>4} | {'Grounded':>8} | {'Tokens':>7} | {'s/ans':>6} |"
    )

    divider = (
        f"|{'-' * 22}|{'-' * 8}|{'-' * 6}|{'-' * 6}|{'-' * 6}"
        f"|{'-' * 10}|{'-' * 9}|{'-' * 8}|"
    )

    lines = [header, divider]

    best = max(result.mean_score for result in results)

    for result in results:
        marker = " *" if result.mean_score == best else ""

        lines.append(
            f"| {result.name + marker:<20} | {result.mean_score:>6.3f} "
            f"| {result.relevant:>4} | {result.partly_relevant:>4} "
            f"| {result.non_relevant:>4} | {result.grounding_rate:>8.3f} "
            f"| {result.tokens_per_answer:>7.0f} | {result.seconds_per_answer:>6.2f} |"
        )

    return "\n".join(lines)
