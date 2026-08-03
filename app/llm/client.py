"""OpenAI chat wrapper.

Thin on purpose — it does one thing beyond calling the API, which is to record
what every call cost in tokens, money and time. Module 5's monitoring dashboard
needs exactly that, and retrofitting it later means touching every call site, so
it is captured from the first request.
"""

from dataclasses import dataclass
import logging
import time

from openai import OpenAI

from app.config import (
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MODEL,
    LLM_PRICING,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
    OPENAI_API_KEY,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMResponse:
    """One completion, plus everything monitoring needs to know about it."""

    text: str
    model: str

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    # gpt-5.x models bill internal reasoning as completion tokens. Tracking it
    # separately is the only way to see when reasoning, rather than the answer,
    # is what is driving cost up.
    reasoning_tokens: int

    latency_seconds: float

    # None when the model's price is not in LLM_PRICING. Reporting an honest
    # "unknown" beats putting a fabricated number on a dashboard.
    cost_usd: float | None

    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        """Whether the model ran out of output budget mid-answer."""

        return self.finish_reason == "length"


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float | None:
    """Cost in USD, or None if this model's pricing is unknown."""

    pricing = LLM_PRICING.get(model)

    if pricing is None:
        # Try the undated base name: "gpt-5.4-mini-2026-03-17" -> "gpt-5.4-mini".
        for known, rates in LLM_PRICING.items():
            if model.startswith(known):
                pricing = rates
                break

    if pricing is None:
        return None

    input_rate, output_rate = pricing

    return (
        prompt_tokens * input_rate / 1_000_000
        + completion_tokens * output_rate / 1_000_000
    )


class LLMClient:

    def __init__(
        self,
        model: str = LLM_MODEL,
        temperature: float = LLM_TEMPERATURE,
        max_output_tokens: int = LLM_MAX_OUTPUT_TOKENS,
    ) -> None:

        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env before asking "
                "questions (see .env.example)."
            )

        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

        self.client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=LLM_TIMEOUT_SECONDS,
        )

        if estimate_cost(model, 1, 1) is None:
            log.warning(
                "No pricing known for %s, so answers will report cost as "
                "unknown. Set LLM_PRICE_INPUT_PER_1M and "
                "LLM_PRICE_OUTPUT_PER_1M in .env to enable cost tracking.",
                model,
            )

    # ---------------------------------------------------------

    def complete(self, system: str, user: str) -> LLMResponse:
        """Send one system+user exchange and return the answer with its usage."""

        started = time.perf_counter()

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
            # Not `max_tokens`: gpt-5.x rejects it outright with a 400. The newer
            # parameter is accepted by gpt-4o-mini too, so one code path covers
            # both old and new models.
            max_completion_tokens=self.max_output_tokens,
        )

        latency = time.perf_counter() - started

        usage = response.usage
        choice = response.choices[0]

        details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0

        result = LLMResponse(
            text=(choice.message.content or "").strip(),
            model=response.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_seconds=latency,
            cost_usd=estimate_cost(
                response.model,
                usage.prompt_tokens,
                usage.completion_tokens,
            ),
            finish_reason=choice.finish_reason,
        )

        if result.truncated:
            log.warning(
                "Answer hit the %s-token output limit and was cut off. "
                "Raise LLM_MAX_OUTPUT_TOKENS.",
                self.max_output_tokens,
            )

        log.debug(
            "LLM %s: %s prompt + %s completion (%s reasoning) tokens in %.2fs",
            result.model,
            result.prompt_tokens,
            result.completion_tokens,
            result.reasoning_tokens,
            latency,
        )

        return result
