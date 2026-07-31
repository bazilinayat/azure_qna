"""Tests for the chunker.

The invariant that matters most is the token budget. If a chunk exceeds the
embedding model's limit, the encoder truncates it silently — there is no error,
no warning, and no way to notice at query time except degraded answers. So that
gets tested from several angles.
"""

import pytest

from app.config import CHUNK_MAX_TOKENS
from app.ingest.chunker import (
    Chunk,
    chunk_document,
    split_blocks,
    split_sections,
)
from app.ingest.tokenizer import get_token_counter


@pytest.fixture(scope="module")
def counter():
    return get_token_counter()


# --------------------------------------------------
# Sections
# --------------------------------------------------

def test_split_sections_tracks_header_hierarchy():
    content = """# Top

intro text

## Alpha

alpha body

### Alpha One

nested body

## Beta

beta body
"""

    sections = split_sections(content)

    paths = [section.headers for section in sections]

    assert ("Top",) in paths
    assert ("Top", "Alpha") in paths
    assert ("Top", "Alpha", "Alpha One") in paths

    # Beta is a sibling of Alpha, so Alpha One must have been popped.
    assert ("Top", "Beta") in paths


def test_hash_inside_code_fence_is_not_a_header():
    content = """# Real Header

```bash
# this is a shell comment, not a heading
az storage account create
```

after
"""

    sections = split_sections(content)

    assert len(sections) == 1
    assert sections[0].headers == ("Real Header",)
    assert "az storage account create" in sections[0].body


# --------------------------------------------------
# Blocks
# --------------------------------------------------

def test_code_fence_stays_one_block():
    body = """intro paragraph

```python
line one

line two after a blank
```

trailing paragraph
"""

    blocks = split_blocks(body)

    code_blocks = [block for block in blocks if block.startswith("```")]

    assert len(code_blocks) == 1

    # The blank line inside the fence must not have split the block.
    assert "line one" in code_blocks[0]
    assert "line two after a blank" in code_blocks[0]


def test_table_stays_one_block():
    body = """before

| Column | Meaning |
|--|--|
| a | first |
| b | second |

after
"""

    blocks = split_blocks(body)

    table_blocks = [block for block in blocks if block.lstrip().startswith("|")]

    assert len(table_blocks) == 1
    assert "first" in table_blocks[0]
    assert "second" in table_blocks[0]


# --------------------------------------------------
# Budget invariant
# --------------------------------------------------

def test_no_chunk_exceeds_budget_on_long_prose(counter):
    paragraph = (
        "Azure Blob Storage stores unstructured object data at scale and "
        "supports hot, cool and archive access tiers for cost management. "
    )

    content = "# Storage Overview\n\n" + "\n\n".join([paragraph * 3] * 40)

    chunks = chunk_document("Storage Overview", content)

    assert chunks

    for chunk in chunks:
        assert chunk.token_count <= CHUNK_MAX_TOKENS
        assert counter.count(chunk.content) <= CHUNK_MAX_TOKENS


def test_unbreakable_token_is_split_by_character(counter):
    """A single whitespace-free token far longer than the budget.

    Both cases below are real: base64 certificate bodies in the VPN Gateway docs
    and URL-encoded Resource Graph queries in the governance samples. Neither has
    a space in it, so splitting on whitespace cannot reduce them at all.
    """

    base64_body = (
        "MIIC/zCCAeugAwIBAgIQKazxzFjMkp9JRiX+tkTfSzAJBgUrDgMCHQUAMBgxFjAU" * 140
    )

    url_encoded = (
        "Resources%0D%0A%7C%20where%20type%20%3D~%20%27microsoft.compute%2F%27" * 100
    )

    for blob in (base64_body, url_encoded):

        assert counter.count(blob) > CHUNK_MAX_TOKENS, "test data is not oversized"

        chunks = chunk_document("Cert", f"# Cert\n\n{blob}\n")

        assert len(chunks) > 1

        for chunk in chunks:
            assert chunk.token_count <= CHUNK_MAX_TOKENS


def test_punctuation_free_mega_word_collapses_to_one_token(counter):
    """Documents a genuinely surprising tokenizer behaviour.

    BERT WordPiece gives up on any "word" over 100 characters and emits a single
    [UNK]. So a 10,000-character unbroken string costs one token, while the same
    length of base64 — which the pre-tokenizer splits on `/` and `+` — costs over
    five thousand. Chunking cannot be reasoned about from character length.
    """

    assert counter.count("A1b2C3d4E5f6" * 900) == 1


def test_oversized_code_fence_is_split_and_refenced(counter):
    body = "\n".join(f"az resource show --ids /very/long/resource/id/{i}" for i in range(400))

    content = f"# Commands\n\n```azurecli\n{body}\n```\n"

    chunks = chunk_document("Commands", content)

    assert len(chunks) > 1

    for chunk in chunks:
        assert chunk.token_count <= CHUNK_MAX_TOKENS

        # Every fragment should still be recognisable as a code block.
        assert "```" in chunk.content


# --------------------------------------------------
# Header path
# --------------------------------------------------

def test_header_path_is_prepended_to_every_chunk():
    content = "# Networking\n\n## Subnets\n\nSubnets divide a virtual network.\n"

    chunks = chunk_document("Virtual Network Overview", content)

    assert chunks

    for chunk in chunks:
        assert chunk.header_path
        assert chunk.content.startswith(chunk.header_path)


def test_duplicate_title_heading_is_not_repeated():
    """Learn articles repeat the frontmatter title as their H1."""

    title = "Introduction to Azure Blob Storage"

    content = f"# {title}\n\nBody text goes here and is long enough to keep.\n"

    chunks = chunk_document(title, content)

    assert chunks

    # The title should appear once in the breadcrumb, not twice.
    assert chunks[0].header_path.count(title) == 1


def test_chunk_indexes_are_contiguous_from_zero():
    paragraph = "Azure Resource Manager templates describe infrastructure. " * 20

    content = "# ARM\n\n" + "\n\n".join([paragraph] * 15)

    chunks = chunk_document("ARM", content)

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))


def test_empty_document_yields_no_chunks():
    assert chunk_document("Empty", "") == []
    assert chunk_document("Empty", "   \n\n  ") == []
