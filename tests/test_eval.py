"""Tests for evaluation and monitoring that do not call the API."""

import pytest

from app.eval.judge import (
    NON_RELEVANT,
    PARTLY_RELEVANT,
    RELEVANT,
    Judgement,
    _parse,
)
from app.eval.ground_truth import GroundTruthItem
from app.eval.retrieval import _metrics, evaluate, format_table
from app.search.search_result import SearchResult


# --------------------------------------------------
# Judge response parsing
# --------------------------------------------------

def test_clean_json_is_parsed():
    relevance, explanation = _parse(
        '{"relevance": "RELEVANT", "explanation": "Answers it directly."}'
    )

    assert relevance == RELEVANT
    assert explanation == "Answers it directly."


def test_json_in_a_code_fence_is_parsed():
    relevance, _ = _parse(
        '```json\n{"relevance": "PARTLY_RELEVANT", "explanation": "x"}\n```'
    )

    assert relevance == PARTLY_RELEVANT


def test_json_with_surrounding_prose_is_parsed():
    relevance, _ = _parse(
        'Here is my assessment:\n'
        '{"relevance": "NON_RELEVANT", "explanation": "Off topic."}\n'
        'Let me know if you need more.'
    )

    assert relevance == NON_RELEVANT


def test_bare_label_is_recovered():
    relevance, _ = _parse("I would rate this PARTLY_RELEVANT overall.")

    assert relevance == PARTLY_RELEVANT


def test_non_relevant_is_not_mistaken_for_relevant():
    """RELEVANT is a substring of both other labels, so order matters."""

    assert _parse("This is NON_RELEVANT.")[0] == NON_RELEVANT
    assert _parse("This is PARTLY_RELEVANT.")[0] == PARTLY_RELEVANT


def test_unparseable_response_fails_closed():
    """An unreadable judgement must not be counted as a pass."""

    relevance, _ = _parse("the model rambled and said nothing useful")

    assert relevance == NON_RELEVANT


def test_judgement_scores():
    assert Judgement(RELEVANT, "").score == 1.0
    assert Judgement(PARTLY_RELEVANT, "").score == 0.5
    assert Judgement(NON_RELEVANT, "").score == 0.0
    assert Judgement(RELEVANT, "").is_relevant is True


# --------------------------------------------------
# Retrieval metrics
# --------------------------------------------------

def test_perfect_retrieval():
    metrics = _metrics("perfect", [1, 1, 1], elapsed=3.0)

    assert metrics.hit_rate == 1.0
    assert metrics.mrr == 1.0
    assert metrics.hit_rate_at_1 == 1.0


def test_total_miss():
    metrics = _metrics("miss", [None, None], elapsed=1.0)

    assert metrics.hit_rate == 0.0
    assert metrics.mrr == 0.0


def test_mrr_rewards_higher_ranks():
    """The whole point of MRR over hit rate: rank 1 must beat rank 5."""

    high = _metrics("high", [1, 1], elapsed=1.0)
    low = _metrics("low", [5, 5], elapsed=1.0)

    assert high.hit_rate == low.hit_rate
    assert high.mrr > low.mrr
    assert low.mrr == pytest.approx(0.2)


def test_mrr_is_averaged_over_all_questions_including_misses():
    # Ranks 1 and 2 found, one missed: (1/1 + 1/2 + 0) / 3
    metrics = _metrics("mixed", [1, 2, None], elapsed=3.0)

    assert metrics.mrr == pytest.approx(0.5)
    assert metrics.hit_rate == pytest.approx(2 / 3)


def test_hit_at_k_thresholds():
    metrics = _metrics("k", [1, 2, 3, 4], elapsed=1.0)

    assert metrics.hit_rate_at_1 == 0.25
    assert metrics.hit_rate_at_3 == 0.75
    assert metrics.hit_rate == 1.0


def test_empty_ground_truth_does_not_divide_by_zero():
    metrics = _metrics("empty", [], elapsed=0.0)

    assert metrics.questions == 0
    assert metrics.hit_rate == 0.0


# --------------------------------------------------
# Evaluation loop
# --------------------------------------------------

class FakeSearch:
    def __init__(self, document_ids):
        self.document_ids = document_ids

    def search(self, query):
        return [
            SearchResult(
                id=document_id,
                document_id=document_id,
                chunk_index=0,
                title="t",
                url="u",
                category="c",
                header_path=None,
                content="body",
                score=1.0,
                source="test",
            )
            for document_id in self.document_ids
        ]


def make_item(document_id: int) -> GroundTruthItem:
    return GroundTruthItem(
        question=f"question about {document_id}",
        document_id=document_id,
        url="u",
        title="t",
        category="c",
    )


def test_evaluate_finds_the_expected_document():
    # The correct document (7) is returned second.
    metrics = evaluate(
        [make_item(7)],
        search=FakeSearch([3, 7, 9]),
        name="fake",
        progress=False,
    )

    assert metrics.ranks == [2]
    assert metrics.mrr == pytest.approx(0.5)


def test_evaluate_records_a_miss():
    metrics = evaluate(
        [make_item(7)],
        search=FakeSearch([1, 2, 3]),
        name="fake",
        progress=False,
    )

    assert metrics.ranks == [None]
    assert metrics.hit_rate == 0.0


def test_a_failing_retriever_counts_as_a_miss_not_a_crash():
    class Broken:
        def search(self, query):
            raise RuntimeError("qdrant is down")

    metrics = evaluate(
        [make_item(1)], search=Broken(), name="broken", progress=False
    )

    assert metrics.ranks == [None]


def test_format_table_marks_the_best_configuration():
    results = [
        _metrics("worse", [3, 3], elapsed=1.0),
        _metrics("better", [1, 1], elapsed=1.0),
    ]

    table = format_table(results)

    assert "better *" in table
    assert "worse *" not in table


# --------------------------------------------------
# Monitoring
# --------------------------------------------------

def test_feedback_rejects_values_other_than_plus_or_minus_one(monkeypatch):
    from app.monitoring import store

    monkeypatch.setattr(store, "MONITORING_ENABLED", True)

    with pytest.raises(ValueError):
        store.log_feedback("some-id", 5)
