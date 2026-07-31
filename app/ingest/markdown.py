"""Parsing and cleaning of Microsoft Learn markdown.

Azure docs are not plain markdown. They carry a large amount of authoring
machinery that is meaningless to a retrieval system and actively harmful in
embeddings: transclusion directives, image/video directives, zone pivots for
per-platform tabs, and relative `.md` links whose paths dominate the token
budget without carrying meaning.

Roughly 5,700 of the ~13,500 articles contain INCLUDE directives and 5,500
contain image directives, so this is not an edge case.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re

import yaml
from yaml import YAMLError

# [!INCLUDE [name](../path/file.md)] — a transclusion pointer with no content.
_INCLUDE = re.compile(r"\[!INCLUDE\s*\[[^\]]*\]\([^)]*\)\]", re.IGNORECASE)

# :::image type="content" source="..." alt-text="...":::  (and :::image-end:::)
_IMAGE_DIRECTIVE = re.compile(r":::\s*image[^:]*:::", re.IGNORECASE)
_IMAGE_END = re.compile(r":::\s*image-end\s*:::", re.IGNORECASE)

# ::: zone pivot="..."  /  ::: zone-end  /  ::: moniker range="..."
# The wrapped content is kept; only the tab markers are dropped.
_ZONE = re.compile(
    r"^\s*:::\s*(zone|zone-end|moniker|moniker-end)[^\n]*$",
    re.IGNORECASE | re.MULTILINE,
)

# :::code language="csharp" source="~/samples/x.cs":::  — external code pointer.
_CODE_DIRECTIVE = re.compile(r":::\s*code[^:]*:::", re.IGNORECASE)

# :::row:::, :::column span="":::, and their -end counterparts (layout only).
_ROW_COLUMN = re.compile(
    r":::\s*(row|row-end|column|column-end)[^:]*:::",
    re.IGNORECASE,
)

# > [!NOTE] / [!TIP] / [!WARNING] / [!IMPORTANT] / [!CAUTION]
# The label is worth keeping as prose — "Warning:" changes how an answer reads.
_ALERT = re.compile(
    r"^\s*>\s*\[!(NOTE|TIP|WARNING|IMPORTANT|CAUTION)\]\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# [display text](target) -> display text. Keeps the prose, drops the path.
_MD_LINK = re.compile(r"\[([^\]^]*?)\]\((?!\s)[^)\s]*(?:\s+\"[^\"]*\")?\)")

# Reference-style link definitions at the bottom of a file: [1]: https://...
_LINK_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.MULTILINE)

_BLANK_LINES = re.compile(r"\n{3,}")

_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)


@dataclass(slots=True)
class ParsedDocument:
    """A source markdown file, split into metadata and cleaned body."""

    title: str
    description: str | None
    content: str
    last_updated: str | None
    frontmatter: dict = field(default_factory=dict)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from the markdown body."""

    if not text.startswith("---"):
        return {}, text

    # The body itself commonly contains `---` horizontal rules, so split on the
    # first two delimiters only.
    parts = text.split("---", 2)

    if len(parts) < 3:
        return {}, text

    try:
        metadata = yaml.safe_load(parts[1]) or {}

    except YAMLError:
        # Malformed frontmatter is not worth failing the document over; the
        # caller logs it and falls back to the filename for the title.
        return {}, parts[2].strip()

    if not isinstance(metadata, dict):
        metadata = {}

    return metadata, parts[2].strip()


def clean_markdown(text: str) -> str:
    """Strip Microsoft Learn authoring directives and link noise."""

    text = _HTML_COMMENT.sub("", text)

    text = _INCLUDE.sub("", text)

    text = _IMAGE_DIRECTIVE.sub("", text)
    text = _IMAGE_END.sub("", text)

    text = _CODE_DIRECTIVE.sub("", text)
    text = _ROW_COLUMN.sub("", text)

    text = _ZONE.sub("", text)

    text = _ALERT.sub(
        lambda match: f"> {match.group(1).capitalize()}:",
        text,
    )

    text = _LINK_DEF.sub("", text)

    # Applied twice: nested constructs like [![alt](img)](target) need a second
    # pass to fully unwrap.
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)

    text = _TRAILING_SPACE.sub("", text)
    text = _BLANK_LINES.sub("\n\n", text)

    return text.strip()


def build_title(frontmatter: dict, file: Path) -> str:
    """Compose a display title from frontmatter, falling back to the filename.

    `titleSuffix` carries the service name ("Azure Storage"), which makes titles
    far more distinguishable once they are prepended to every chunk.
    """

    title = frontmatter.get("title")

    if not isinstance(title, str) or not title.strip():
        return file.stem.replace("-", " ")

    title = title.strip()

    suffix = frontmatter.get("titleSuffix")

    if isinstance(suffix, str) and suffix.strip():
        suffix = suffix.strip()

        if suffix.lower() not in title.lower():
            title = f"{title} - {suffix}"

    return title


def parse_file(file: Path) -> ParsedDocument:
    """Read, parse and clean a single markdown file."""

    raw = file.read_text(encoding="utf-8", errors="ignore")

    frontmatter, body = parse_frontmatter(raw)

    date = frontmatter.get("ms.date")

    return ParsedDocument(
        title=build_title(frontmatter, file),
        description=(
            frontmatter.get("description").strip()
            if isinstance(frontmatter.get("description"), str)
            else None
        ),
        content=clean_markdown(body),
        last_updated=str(date) if date is not None else None,
        frontmatter=frontmatter,
    )
