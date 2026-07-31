"""Tests for Microsoft Learn markdown parsing and cleaning."""

from app.ingest.markdown import (
    build_title,
    clean_markdown,
    parse_frontmatter,
)


# --------------------------------------------------
# Frontmatter
# --------------------------------------------------

def test_parse_frontmatter_extracts_metadata_and_body():
    text = """---
title: Introduction to Blob Storage
description: Learn about blobs.
ms.date: 05/15/2026
---

# Heading

Body text.
"""

    metadata, body = parse_frontmatter(text)

    assert metadata["title"] == "Introduction to Blob Storage"
    assert metadata["ms.date"] == "05/15/2026"
    assert body.startswith("# Heading")


def test_horizontal_rule_in_body_does_not_break_parsing():
    """`---` is also a markdown horizontal rule, so only the first two count."""

    text = """---
title: Test
---

Intro paragraph.

---

After the rule.
"""

    metadata, body = parse_frontmatter(text)

    assert metadata["title"] == "Test"
    assert "After the rule." in body


def test_malformed_frontmatter_does_not_raise():
    text = """---
title: [unclosed
  bad: : yaml
---

Body survives.
"""

    metadata, body = parse_frontmatter(text)

    assert metadata == {}
    assert "Body survives." in body


def test_document_without_frontmatter_is_returned_whole():
    text = "# Just a heading\n\nAnd a body.\n"

    metadata, body = parse_frontmatter(text)

    assert metadata == {}
    assert body == text


# --------------------------------------------------
# Cleaning
# --------------------------------------------------

def test_include_directive_is_removed():
    text = "Before\n\n[!INCLUDE [name](../../includes/some-fragment.md)]\n\nAfter"

    cleaned = clean_markdown(text)

    assert "INCLUDE" not in cleaned
    assert "Before" in cleaned
    assert "After" in cleaned


def test_image_directive_is_removed():
    text = (
        'Text before.\n\n'
        ':::image type="content" source="./media/x.png" alt-text="A diagram.":::\n\n'
        'Text after.'
    )

    cleaned = clean_markdown(text)

    assert ":::image" not in cleaned
    assert "Text before." in cleaned
    assert "Text after." in cleaned


def test_zone_pivot_markers_removed_but_content_kept():
    text = """::: zone pivot="azure-cli"

Run the az command.

::: zone-end
"""

    cleaned = clean_markdown(text)

    assert "::: zone" not in cleaned
    assert "Run the az command." in cleaned


def test_alert_label_becomes_prose():
    text = "> [!WARNING]\n> Deleting this is permanent.\n"

    cleaned = clean_markdown(text)

    assert "[!WARNING]" not in cleaned
    assert "Warning:" in cleaned
    assert "Deleting this is permanent." in cleaned


def test_relative_links_are_unwrapped_to_their_text():
    text = "See [Create a storage account](../common/storage-account-create.md) first."

    cleaned = clean_markdown(text)

    assert "storage-account-create.md" not in cleaned
    assert "See Create a storage account first." in cleaned


def test_html_comments_are_removed():
    text = "Visible.\n\n<!-- reviewer note: fix this later -->\n\nAlso visible."

    cleaned = clean_markdown(text)

    assert "reviewer note" not in cleaned
    assert "Visible." in cleaned


def test_code_blocks_and_tables_survive_cleaning():
    text = """```azurecli
az storage account create --name mystorageaccount
```

| Tier | Cost |
|--|--|
| Hot | High |
"""

    cleaned = clean_markdown(text)

    assert "az storage account create" in cleaned
    assert "| Hot | High |" in cleaned


# --------------------------------------------------
# Titles
# --------------------------------------------------

def test_title_suffix_is_appended(tmp_path):
    file = tmp_path / "storage-blobs-introduction.md"

    title = build_title(
        {"title": "Introduction to Blob Storage", "titleSuffix": "Azure Storage"},
        file,
    )

    assert title == "Introduction to Blob Storage - Azure Storage"


def test_redundant_title_suffix_is_not_appended(tmp_path):
    file = tmp_path / "x.md"

    title = build_title(
        {"title": "Azure Storage overview", "titleSuffix": "Azure Storage"},
        file,
    )

    assert title == "Azure Storage overview"


def test_missing_title_falls_back_to_filename(tmp_path):
    file = tmp_path / "storage-blobs-introduction.md"

    assert build_title({}, file) == "storage blobs introduction"
