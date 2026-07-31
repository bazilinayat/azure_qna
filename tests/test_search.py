"""Tests for FTS query construction and rank fusion.

Both are places where a bug is quiet rather than loud: a malformed FTS expression
raises inside a search that returns an empty list, and a fusion bug just reorders
results plausibly.
"""

from app.db import fts
from app.search.query_expander import QueryExpander
from app.search.rrf import ReciprocalRankFusion
from app.search.search_result import SearchResult


def make_result(result_id: int, score: float = 0.0) -> SearchResult:
    return SearchResult(
        id=result_id,
        document_id=result_id * 10,
        chunk_index=0,
        title=f"doc {result_id}",
        url=f"https://example.com/{result_id}",
        category="storage",
        header_path="A > B",
        content=f"content {result_id}",
        score=score,
        source="test",
    )


# --------------------------------------------------
# FTS5 query building
# --------------------------------------------------

def test_plain_query_becomes_quoted_or_terms():
    assert fts.build_match_query("blob storage") == '"blob" OR "storage"'


def test_double_quotes_cannot_escape_the_expression():
    """A bare quote in user input is an FTS5 syntax error."""

    built = fts.build_match_query('blob" OR "x')

    # Every term is a quoted literal; no stray quote survives.
    assert built == '"blob" OR "OR" OR "x"'


def test_fts5_operators_are_neutralised():
    for hostile in (
        "blob NEAR/2 storage",
        "blob*",
        "-blob",
        "blob AND NOT storage",
        "(blob OR storage)",
        'column:blob',
    ):
        built = fts.build_match_query(hostile)

        # Only quoted terms and the OR joiner may appear.
        stripped = built.replace(" OR ", " ")

        for token in stripped.split():
            assert token.startswith('"') and token.endswith('"'), built


def test_punctuation_only_query_returns_empty():
    assert fts.build_match_query("???") == ""
    assert fts.build_match_query("") == ""
    assert fts.build_match_query("   ") == ""


def test_underscores_and_digits_are_kept():
    built = fts.build_match_query("Standard_LRS v2")

    assert '"Standard_LRS"' in built
    assert '"v2"' in built


# --------------------------------------------------
# Reciprocal rank fusion
# --------------------------------------------------

def test_result_ranked_well_in_both_lists_wins():
    keyword = [make_result(1), make_result(2), make_result(3)]
    vector = [make_result(4), make_result(2), make_result(5)]

    fused = ReciprocalRankFusion(k=60).fuse(keyword, vector)

    # Result 2 is second in both rankings; nothing else appears twice.
    assert fused[0].id == 2


def test_fusion_deduplicates():
    keyword = [make_result(1), make_result(2)]
    vector = [make_result(2), make_result(1)]

    fused = ReciprocalRankFusion().fuse(keyword, vector)

    assert sorted(result.id for result in fused) == [1, 2]


def test_fusion_handles_empty_and_single_rankings():
    assert ReciprocalRankFusion().fuse() == []
    assert ReciprocalRankFusion().fuse([]) == []

    single = ReciprocalRankFusion().fuse([make_result(7)])

    assert [result.id for result in single] == [7]


def test_fusion_scores_are_descending():
    fused = ReciprocalRankFusion().fuse(
        [make_result(1), make_result(2), make_result(3)],
        [make_result(3), make_result(1)],
    )

    scores = [result.score for result in fused]

    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------
# Query expansion
# --------------------------------------------------

def test_acronym_is_substituted_into_the_original_query():
    variants = QueryExpander().expand("how do I resize a vm")

    assert "how do I resize a vm" in variants
    assert "how do I resize a virtual machine" in variants


def test_expansion_does_not_emit_bare_terms():
    """Every variant must still be the user's question, not a stray keyword."""

    variants = QueryExpander().expand("how do I secure a blob")

    for variant in variants:
        assert "how do I secure" in variant


def test_acronym_inside_a_longer_word_does_not_match():
    variants = QueryExpander().expand("migrate from vmware to azure")

    assert variants == ["migrate from vmware to azure"]


def test_query_without_acronyms_is_unchanged():
    variants = QueryExpander().expand("what is a resource group")

    assert variants == ["what is a resource group"]


def test_variant_count_is_capped():
    expander = QueryExpander(max_variants=2)

    variants = expander.expand("aad and entra and rbac and nsg and vnet")

    assert len(variants) <= 2
