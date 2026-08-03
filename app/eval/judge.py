"""LLM-as-a-judge relevance scoring.

One judge, two callers. Offline it scores answers generated over the ground
truth set so prompt and model variants can be compared; online the Streamlit app
scores each live answer so the dashboard has a quality signal that does not
depend on users bothering to click thumbs-up.

Using the same judge for both is deliberate — an offline score that is measured
differently from the live score cannot be compared to it, which defeats the point
of measuring either.
"""

from dataclasses import dataclass
import json
import logging

from app.config import JUDGE_MODEL
from app.llm.client import LLMClient

log = logging.getLogger(__name__)

RELEVANT = "RELEVANT"
PARTLY_RELEVANT = "PARTLY_RELEVANT"
NON_RELEVANT = "NON_RELEVANT"

VALID_RELEVANCE = {RELEVANT, PARTLY_RELEVANT, NON_RELEVANT}

_SYSTEM = """
You are an impartial evaluator of a question-answering system built on Microsoft
Azure documentation.

Judge only how well the ANSWER addresses the QUESTION. Ignore style, length and
tone. Do not use your own knowledge of Azure to check whether the answer is
factually right -- you are grading relevance to the question, not correctness.

Classify as exactly one of:
- RELEVANT: the answer directly and completely addresses the question.
- PARTLY_RELEVANT: the answer is on topic but incomplete, hedged, or answers a
  neighbouring question rather than the one asked.
- NON_RELEVANT: the answer does not address the question, or declines to answer.

Respond with a JSON object and nothing else:
{"relevance": "...", "explanation": "one sentence"}
""".strip()

_USER = """
QUESTION:
{question}

ANSWER:
{answer}
""".strip()


@dataclass(slots=True)
class Judgement:
    relevance: str
    explanation: str

    model: str = ""
    total_tokens: int = 0
    cost_usd: float | None = None

    @property
    def is_relevant(self) -> bool:
        return self.relevance == RELEVANT

    @property
    def score(self) -> float:
        """Numeric form for averaging: 1.0, 0.5 or 0.0."""

        return {
            RELEVANT: 1.0,
            PARTLY_RELEVANT: 0.5,
            NON_RELEVANT: 0.0,
        }.get(self.relevance, 0.0)


class RelevanceJudge:

    def __init__(self, model: str = JUDGE_MODEL, client: LLMClient | None = None):
        self.model = model
        self._client = client

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            # Temperature 0: a judge that disagrees with itself between runs
            # cannot be used to compare two systems.
            self._client = LLMClient(model=self.model, temperature=0.0)

        return self._client

    def judge(self, question: str, answer: str) -> Judgement:

        if not answer.strip():
            return Judgement(
                relevance=NON_RELEVANT,
                explanation="The answer was empty.",
                model=self.model,
            )

        response = self.client.complete(
            _SYSTEM,
            _USER.format(question=question, answer=answer),
        )

        relevance, explanation = _parse(response.text)

        return Judgement(
            relevance=relevance,
            explanation=explanation,
            model=response.model,
            total_tokens=response.total_tokens,
            cost_usd=response.cost_usd,
        )


def _parse(text: str) -> tuple[str, str]:
    """Pull the verdict out of the model's reply.

    Models wrap JSON in prose or code fences often enough that trusting
    json.loads on the raw text produces intermittent failures, and an evaluation
    that silently drops rows is worse than one that is slightly lenient.
    """

    cleaned = text.strip()

    if cleaned.startswith("```"):
        lines = [
            line
            for line in cleaned.splitlines()
            if not line.strip().startswith("```")
        ]
        cleaned = "\n".join(lines).strip()

    try:
        start = cleaned.index("{")
        end = cleaned.rindex("}") + 1

        payload = json.loads(cleaned[start:end])

        relevance = str(payload.get("relevance", "")).strip().upper()
        explanation = str(payload.get("explanation", "")).strip()

        if relevance in VALID_RELEVANCE:
            return relevance, explanation

    except (ValueError, json.JSONDecodeError):
        pass

    # Fall back to looking for the label anywhere in the reply. Check the
    # two-word labels first, since "RELEVANT" is a substring of both.
    upper = cleaned.upper()

    for label in (NON_RELEVANT, PARTLY_RELEVANT, RELEVANT):
        if label in upper:
            return label, "Recovered from an unparseable judge response."

    log.warning("Could not parse judge response: %r", text[:200])

    return NON_RELEVANT, "The judge response could not be parsed."
