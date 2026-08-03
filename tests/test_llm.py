"""Tests for the LLM layer that do not call the API.

Everything here is deterministic and free to run. The API-dependent behaviour
(answer quality, groundedness) is what the LLM evaluation will cover; these
tests cover the plumbing around it, which is where silent mistakes live.
"""

import pytest

from app.llm.client import LLMResponse, estimate_cost
from app.llm.prompts import PROMPTS, get_prompt
from app.llm.rag import AnswerResult, RagPipeline, build_context
from app.search.search_result import SearchResult


def make_result(result_id: int, title: str = "Blob Storage") -> SearchResult:
    return SearchResult(
        id=result_id,
        document_id=result_id,
        chunk_index=0,
        title=title,
        url=f"https://learn.microsoft.com/azure/storage/{result_id}",
        category="storage",
        header_path=f"{title} > Section {result_id}",
        content=f"Body text for chunk {result_id}.",
        score=1.0,
        source="rrf",
    )


# --------------------------------------------------
# Context building
# --------------------------------------------------

def test_context_numbers_sources_from_one():
    """Citation numbering is the link between an answer and a real URL."""

    context = build_context([make_result(1), make_result(2), make_result(3)])

    assert "[1]" in context
    assert "[2]" in context
    assert "[3]" in context
    assert "[0]" not in context


def test_context_includes_url_and_content_for_each_source():
    context = build_context([make_result(7)])

    assert "https://learn.microsoft.com/azure/storage/7" in context
    assert "Body text for chunk 7." in context


def test_empty_context_is_empty_string():
    assert build_context([]) == ""


# --------------------------------------------------
# Prompts
# --------------------------------------------------

def test_every_prompt_renders_question_and_context():
    for name in PROMPTS:
        template = get_prompt(name)

        system, user = template.render("How do I resize a disk?", "CTX-MARKER")

        assert system.strip()
        assert "How do I resize a disk?" in user
        assert "CTX-MARKER" in user


def test_every_prompt_forbids_outside_knowledge():
    """The grounding instruction is the whole point; catch a prompt losing it."""

    for name in PROMPTS:
        system = get_prompt(name).system.lower()

        assert "context" in system
        assert "only" in system


def test_unknown_prompt_name_raises():
    with pytest.raises(ValueError, match="Unknown prompt"):
        get_prompt("does_not_exist")


# --------------------------------------------------
# Cost estimation
# --------------------------------------------------

def test_cost_is_computed_for_a_known_model():
    # gpt-4o-mini: $0.15 per 1M input, $0.60 per 1M output.
    cost = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)

    assert cost == pytest.approx(0.75)


def test_dated_model_id_resolves_to_its_base_price():
    """The API returns "gpt-4o-mini-2024-07-18", not "gpt-4o-mini"."""

    assert estimate_cost("gpt-4o-mini-2024-07-18", 1000, 1000) == pytest.approx(
        estimate_cost("gpt-4o-mini", 1000, 1000)
    )


def test_unknown_model_returns_none_rather_than_guessing():
    """A fabricated cost on a dashboard is worse than an absent one."""

    assert estimate_cost("some-unreleased-model", 1000, 1000) is None


# --------------------------------------------------
# Pipeline behaviour
# --------------------------------------------------

class FakeSearch:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self.results


class ExplodingClient:
    """Any call to this is a test failure."""

    def complete(self, system, user):
        raise AssertionError("The LLM must not be called without context")


def test_no_retrieval_means_no_llm_call():
    """Answering with zero context is exactly the hallucination we exist to avoid."""

    pipeline = RagPipeline(search=FakeSearch([]), client=ExplodingClient())

    result = pipeline.answer("something not in the docs")

    assert result.sources == []
    assert result.llm_response is None
    assert "could not find" in result.answer.lower()


def test_context_is_capped_at_the_configured_chunk_count():
    many = [make_result(i) for i in range(20)]

    pipeline = RagPipeline(
        search=FakeSearch(many),
        client=ExplodingClient(),
        context_chunks=3,
    )

    sources, _ = pipeline.retrieve("anything")

    assert len(sources) == 3


def test_blank_question_is_rejected():
    pipeline = RagPipeline(search=FakeSearch([]), client=ExplodingClient())

    with pytest.raises(ValueError):
        pipeline.answer("   ")


# --------------------------------------------------
# Result object
# --------------------------------------------------

def make_llm_response(**overrides) -> LLMResponse:
    defaults = dict(
        text="answer",
        model="gpt-4o-mini",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        reasoning_tokens=0,
        latency_seconds=2.0,
        cost_usd=0.001,
        finish_reason="stop",
    )
    defaults.update(overrides)

    return LLMResponse(**defaults)


def test_total_seconds_sums_retrieval_and_generation():
    result = AnswerResult(
        question="q",
        answer="a",
        retrieval_seconds=1.5,
        llm_response=make_llm_response(latency_seconds=2.5),
    )

    assert result.total_seconds == pytest.approx(4.0)


def test_truncation_is_detected_from_finish_reason():
    assert make_llm_response(finish_reason="length").truncated is True
    assert make_llm_response(finish_reason="stop").truncated is False


def test_only_cited_sources_are_reported():
    result = AnswerResult(
        question="q",
        answer="Set the flag [3], then verify it [1].",
        sources=[make_result(i) for i in range(1, 6)],
    )

    # In order of first use, not index order.
    assert result.cited_indices() == [3, 1]
    assert [source.id for source in result.cited_sources()] == [3, 1]


def test_refusal_cites_nothing_even_though_chunks_were_retrieved():
    """The misleading case: retrieval returns its top-k regardless of relevance."""

    result = AnswerResult(
        question="q",
        answer="The indexed documentation does not cover this.",
        sources=[make_result(i) for i in range(1, 6)],
    )

    assert result.cited_indices() == []
    assert result.cited_sources() == []


def test_out_of_range_citation_is_ignored():
    """Models occasionally invent a [9] when given five sources."""

    result = AnswerResult(
        question="q",
        answer="See [9] and [2].",
        sources=[make_result(i) for i in range(1, 4)],
    )

    assert result.cited_indices() == [2]


def test_cited_urls_are_unique_and_ordered():
    duplicate = make_result(1)

    result = AnswerResult(
        question="q",
        answer="a",
        sources=[make_result(1), make_result(2), duplicate],
    )

    urls = result.cited_urls()

    assert len(urls) == 2
    assert urls[0].endswith("/1")
    assert urls[1].endswith("/2")
