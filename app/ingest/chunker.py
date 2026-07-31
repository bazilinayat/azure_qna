"""Structure-aware chunking for Microsoft Learn markdown.

The previous approach sliced the token stream into fixed 512-token windows. That
has three problems on this corpus:

1.  It counted with tiktoken while embedding with bge, so chunks overflowed the
    model's 512-token limit and were silently truncated (see `tokenizer.py`).
2.  It split mid-code-block, mid-table and mid-procedure. Azure docs are
    heavily structured, and half a `az` command or half a prerequisites list is
    worse than useless as a retrieval unit.
3.  Chunks arrived context-free. "Set this value to 3" means nothing without
    knowing it sits under "Blob Storage > Lifecycle management > Rule filters".

So instead: split on markdown headers, pack whole blocks up to a token budget,
never break a fenced code block or table unless it alone exceeds the budget, and
prepend the header breadcrumb to every chunk. The breadcrumb helps both halves
of hybrid search — it gives the embedding topical grounding and gives BM25 the
service and feature names to match on.
"""

from dataclasses import dataclass
import logging
import re

from app.config import (
    CHUNK_MAX_TOKENS,
    CHUNK_MIN_TOKENS,
    CHUNK_OVERLAP_TOKENS,
)
from app.ingest.tokenizer import get_token_counter

log = logging.getLogger(__name__)

_HEADER = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

_FENCE = re.compile(r"^\s*(```+|~~~+)(.*)$")

# Tokens reserved for [CLS]/[SEP] and for the newlines joining blocks.
_SPECIAL_TOKEN_MARGIN = 8


@dataclass(slots=True)
class Chunk:
    """A single retrievable unit."""

    chunk_index: int
    header_path: str
    content: str
    token_count: int


@dataclass(slots=True)
class Section:
    """A run of body text under one header breadcrumb."""

    headers: tuple[str, ...]
    body: str


# --------------------------------------------------
# Splitting into sections
# --------------------------------------------------

def split_sections(content: str) -> list[Section]:
    """Split markdown into sections keyed by their header breadcrumb.

    Fenced code is tracked so that comment lines starting with `#` inside a bash
    or YAML block are not mistaken for headers.
    """

    sections: list[Section] = []

    header_stack: list[tuple[int, str]] = []
    current: list[str] = []

    fence: str | None = None

    def flush() -> None:
        body = "\n".join(current).strip()

        if body:
            sections.append(
                Section(
                    headers=tuple(title for _, title in header_stack),
                    body=body,
                )
            )

        current.clear()

    for line in content.splitlines():

        fence_match = _FENCE.match(line)

        if fence_match:
            marker = fence_match.group(1)

            if fence is None:
                fence = marker

            elif line.strip().startswith(fence):
                fence = None

            current.append(line)
            continue

        if fence is not None:
            current.append(line)
            continue

        header_match = _HEADER.match(line)

        if not header_match:
            current.append(line)
            continue

        flush()

        level = len(header_match.group(1))
        title = header_match.group(2).strip()

        # Pop any headers at or below this level, then push this one.
        while header_stack and header_stack[-1][0] >= level:
            header_stack.pop()

        header_stack.append((level, title))

    flush()

    return sections


# --------------------------------------------------
# Splitting a section into indivisible blocks
# --------------------------------------------------

def split_blocks(body: str) -> list[str]:
    """Split section text into blocks that should not be broken apart.

    Fenced code blocks and markdown tables are emitted whole. Everything else is
    split on blank lines, which keeps bullet lists and numbered procedures
    together because Learn articles do not blank-separate their list items.
    """

    blocks: list[str] = []
    buffer: list[str] = []

    fence: str | None = None
    in_table = False

    def flush() -> None:
        text = "\n".join(buffer).strip()

        if text:
            blocks.append(text)

        buffer.clear()

    for line in body.splitlines():

        fence_match = _FENCE.match(line)

        if fence_match:
            marker = fence_match.group(1)

            if fence is None:
                # Starting a code block: close whatever came before it.
                flush()
                fence = marker
                buffer.append(line)

            elif line.strip().startswith(fence):
                buffer.append(line)
                fence = None
                flush()

            else:
                buffer.append(line)

            continue

        if fence is not None:
            buffer.append(line)
            continue

        is_table_row = line.lstrip().startswith("|")

        if is_table_row and not in_table:
            flush()
            in_table = True

        elif in_table and not is_table_row:
            flush()
            in_table = False

        if not line.strip() and not in_table:
            flush()
            continue

        buffer.append(line)

    flush()

    return blocks


# --------------------------------------------------
# Packing blocks into chunks
# --------------------------------------------------

def _pack_units(
    units: list[str],
    counts: list[int],
    budget: int,
    separator: str,
) -> list[str]:
    """Greedily group units so each group stays within `budget` tokens.

    Group sizes are estimated by summing per-unit counts, which slightly
    overestimates the joined length (subword merges across boundaries are lost).
    Erring small is the safe direction: it cannot cause model truncation.
    """

    groups: list[str] = []

    current: list[str] = []
    total = 0

    for unit, count in zip(units, counts):

        if current and total + count > budget:
            groups.append(separator.join(current))
            current = []
            total = 0

        current.append(unit)
        total += count

    if current:
        groups.append(separator.join(current))

    return groups


def _hard_split(text: str, budget: int) -> list[str]:
    """Break a single oversized block down to fit the budget.

    Tried in order of decreasing structural damage: by line, then by word. A
    fenced code block is re-fenced on every fragment so each piece is still
    recognisable — and renderable — as code.
    """

    counter = get_token_counter()

    fence_match = _FENCE.match(text.splitlines()[0]) if text else None

    if fence_match:
        lines = text.splitlines()

        marker = fence_match.group(1)
        info = fence_match.group(2).strip()

        # Drop the opening fence and the closing one, split the body, re-fence.
        inner = lines[1:]

        if inner and inner[-1].strip().startswith(marker):
            inner = inner[:-1]

        opener = f"{marker}{info}"

        # Budget must account for the fence lines added back to each fragment.
        fence_cost = counter.count(f"{opener}\n{marker}")

        inner_budget = max(budget - fence_cost, 1)

        groups = _pack_units(
            inner,
            counter.count_many(inner),
            inner_budget,
            "\n",
        )

        # A single line inside the fence can still exceed the budget on its own
        # (a base64 blob, a very long az command), so oversized groups recurse.
        fragments: list[str] = []

        for group in groups:
            if counter.count(group) > inner_budget:
                fragments.extend(_split_words(group, inner_budget))
            else:
                fragments.append(group)

        return [f"{opener}\n{fragment}\n{marker}" for fragment in fragments]

    lines = text.splitlines()

    if len(lines) > 1:
        groups = _pack_units(lines, counter.count_many(lines), budget, "\n")

        # Any fragment that is still oversized is a single very long line.
        result: list[str] = []

        for group in groups:
            if counter.count(group) > budget and len(group.splitlines()) == 1:
                result.extend(_split_words(group, budget))
            else:
                result.append(group)

        return result

    return _split_words(text, budget)


def _split_words(text: str, budget: int) -> list[str]:
    """Split one long line on whitespace, falling back to characters."""

    counter = get_token_counter()

    words = text.split()

    if not words:
        return []

    groups = _pack_units(words, counter.count_many(words), budget, " ")

    result: list[str] = []

    for group in groups:
        if counter.count(group) > budget:
            result.extend(_split_characters(group, budget))
        else:
            result.append(group)

    return result


def _split_characters(text: str, budget: int) -> list[str]:
    """Absolute last resort: split a single unbreakable token by character.

    Real cases in this corpus: a base64 certificate body (506 tokens on one
    line) and URL-encoded Resource Graph queries (915 tokens, no whitespace at
    all). Whitespace splitting cannot reduce those, so without this the chunk
    would ship over budget and be silently truncated by the encoder.
    """

    counter = get_token_counter()

    total = counter.count(text)

    if total <= budget:
        return [text]

    # Characters per token for this specific text, with headroom.
    ratio = len(text) / max(total, 1)
    window = max(int(budget * ratio * 0.9), 1)

    fragments: list[str] = []

    start = 0

    while start < len(text):

        piece = text[start : start + window]

        # Shrink until it fits. Always leaves at least one character, so `start`
        # strictly advances and this cannot loop forever.
        while counter.count(piece) > budget and len(piece) > 1:
            piece = piece[: max(int(len(piece) * 0.8), 1)]

        fragments.append(piece)

        start += len(piece)

    return fragments


@dataclass(slots=True)
class _Unit:
    """One indivisible block, tagged with the header stack it belongs to."""

    headers: tuple[str, ...]
    text: str
    tokens: int


def chunk_document(
    title: str,
    content: str,
    max_tokens: int = CHUNK_MAX_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
    min_tokens: int = CHUNK_MIN_TOKENS,
) -> list[Chunk]:
    """Split one document into retrievable chunks.

    Packing runs across the whole document rather than restarting at every
    header. Starting fresh per section sounds tidier but fragments the corpus
    badly: Learn articles nest down to `####`, so a per-section packer produced
    ~14 chunks per document with a median of 153 tokens — a third of the budget
    wasted and context scattered across neighbours.

    Consecutive sections are therefore merged while they fit. The chunk's
    breadcrumb becomes the deepest header path common to everything inside it,
    and each section boundary within the chunk keeps its own markdown heading so
    no local structure is lost.
    """

    counter = get_token_counter()

    if not content.strip():
        return []

    units = _build_units(title, content, counter, max_tokens, min_tokens)

    if not units:
        return []

    chunks = _pack_units_into_chunks(
        title, units, counter, max_tokens, overlap_tokens
    )

    chunks = _merge_slivers(chunks, min_tokens, max_tokens, counter)

    return _enforce_limit(chunks, max_tokens, counter)


def _build_units(
    title: str,
    content: str,
    counter,
    max_tokens: int,
    min_tokens: int,
) -> list[_Unit]:
    """Flatten the document into header-tagged, budget-sized blocks."""

    units: list[_Unit] = []

    for section in split_sections(content):

        blocks = split_blocks(section.body)

        if not blocks:
            continue

        # Worst-case prefix cost for this section, used only to decide whether a
        # block needs exploding. The real breadcrumb can only be shorter.
        prefix_cost = (
            counter.count(_build_header_path(title, section.headers))
            + _SPECIAL_TOKEN_MARGIN
        )

        budget = max(max_tokens - prefix_cost, min_tokens)

        for block, count in zip(blocks, counter.count_many(blocks)):

            if count <= budget:
                units.append(_Unit(section.headers, block, count))
                continue

            fragments = _hard_split(block, budget)

            for fragment, fragment_count in zip(
                fragments, counter.count_many(fragments)
            ):
                units.append(_Unit(section.headers, fragment, fragment_count))

    return units


def _pack_units_into_chunks(
    title: str,
    units: list[_Unit],
    counter,
    max_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:

    chunks: list[Chunk] = []

    current: list[_Unit] = []

    def budget_for(candidate: list[_Unit]) -> int:
        """Token budget for the body, given the breadcrumb this chunk will carry."""

        headers = _common_headers([unit.headers for unit in candidate])

        prefix = _build_header_path(title, headers)

        return max_tokens - counter.count(prefix) - _SPECIAL_TOKEN_MARGIN

    def body_tokens(candidate: list[_Unit]) -> int:
        """Body size including the inline headings inserted at section changes."""

        total = sum(unit.tokens for unit in candidate)

        previous: tuple[str, ...] | None = None

        for unit in candidate:
            if previous is not None and unit.headers != previous:
                total += counter.count(_inline_heading(unit.headers))

            previous = unit.headers

        return total

    def flush() -> None:
        if not current:
            return

        headers = _common_headers([unit.headers for unit in current])
        header_path = _build_header_path(title, headers)

        body = _render_body(current, skip_headers=len(headers))

        text = f"{header_path}\n\n{body}" if header_path else body

        chunks.append(
            Chunk(
                chunk_index=len(chunks),
                header_path=header_path,
                content=text,
                token_count=counter.count(text),
            )
        )

    for unit in units:

        candidate = current + [unit]

        if current and body_tokens(candidate) > budget_for(candidate):
            flush()

            current = _overlap_units(current, counter, overlap_tokens)

            # The overlap tail plus this unit can itself exceed the budget, so
            # the check is repeated rather than assumed. Missing this produced
            # chunks of up to 931 tokens against a 480 budget.
            if current and body_tokens(current + [unit]) > budget_for(current + [unit]):
                current = []

        current.append(unit)

    flush()

    return chunks


def _inline_heading(headers: tuple[str, ...]) -> str:
    """Markdown heading line for a section boundary inside a chunk."""

    if not headers:
        return ""

    level = min(len(headers), 6)

    return f"{'#' * level} {headers[-1]}"


def _render_body(units: list[_Unit], skip_headers: int) -> str:
    """Join units, re-inserting headings wherever the section changes.

    `skip_headers` is the depth already covered by the chunk's breadcrumb, so
    those levels are not repeated inside the body.
    """

    parts: list[str] = []

    previous: tuple[str, ...] | None = None

    for unit in units:

        if previous is not None and unit.headers != previous:
            if len(unit.headers) > skip_headers:
                parts.append(_inline_heading(unit.headers))

        parts.append(unit.text)

        previous = unit.headers

    return "\n\n".join(part for part in parts if part)


def _common_headers(stacks: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Longest header prefix shared by every unit in a chunk."""

    if not stacks:
        return ()

    common = stacks[0]

    for stack in stacks[1:]:
        limit = min(len(common), len(stack))

        index = 0
        while index < limit and common[index] == stack[index]:
            index += 1

        common = common[:index]

        if not common:
            break

    return common


def _overlap_units(units: list[_Unit], counter, budget: int) -> list[_Unit]:
    """Trailing units to carry into the next chunk as overlap."""

    if budget <= 0 or len(units) < 2:
        return []

    tail: list[_Unit] = []
    total = 0

    for unit in reversed(units):

        if total + unit.tokens > budget or len(tail) >= len(units) - 1:
            break

        tail.insert(0, unit)
        total += unit.tokens

    return tail


def _enforce_limit(chunks: list[Chunk], max_tokens: int, counter) -> list[Chunk]:
    """Final guarantee that nothing exceeds the budget.

    The packer should already ensure this; this pass exists so a future edit to
    the packing logic cannot silently reintroduce truncated chunks, which are
    invisible at query time.
    """

    result: list[Chunk] = []

    for chunk in chunks:

        if chunk.token_count <= max_tokens:
            chunk.chunk_index = len(result)
            result.append(chunk)
            continue

        log.debug(
            "Chunk over budget (%s > %s), splitting: %s",
            chunk.token_count,
            max_tokens,
            chunk.header_path,
        )

        prefix = chunk.header_path
        prefix_cost = counter.count(prefix) + _SPECIAL_TOKEN_MARGIN

        body = chunk.content

        if prefix and body.startswith(prefix):
            body = body[len(prefix):].lstrip("\n")

        for fragment in _hard_split(body, max(max_tokens - prefix_cost, 1)):

            text = f"{prefix}\n\n{fragment}" if prefix else fragment

            result.append(
                Chunk(
                    chunk_index=len(result),
                    header_path=prefix,
                    content=text,
                    token_count=counter.count(text),
                )
            )

    return result


def _build_header_path(title: str, headers: tuple[str, ...]) -> str:
    """Join the document title and header stack into a breadcrumb.

    Learn articles almost always repeat the frontmatter title as their `#`
    heading, so that duplicate is dropped.
    """

    parts = [title.strip()] if title and title.strip() else []

    for header in headers:
        header = header.strip()

        if not header:
            continue

        if parts and header.lower() == parts[-1].lower():
            continue

        parts.append(header)

    return " > ".join(parts)


def _merge_slivers(
    chunks: list[Chunk],
    min_tokens: int,
    max_tokens: int,
    counter,
) -> list[Chunk]:
    """Fold undersized chunks into the previous one where it fits.

    Learn articles are full of one-line sections ("## Next steps"). On their own
    they are noise in the index; appended to their predecessor they are harmless.
    """

    if not chunks:
        return chunks

    merged: list[Chunk] = []

    for chunk in chunks:

        if (
            merged
            and chunk.token_count < min_tokens
            and merged[-1].header_path == chunk.header_path
            and merged[-1].token_count + chunk.token_count <= max_tokens
        ):
            previous = merged[-1]

            # The breadcrumb is already at the top of `previous`; strip the
            # duplicate copy off the front of the chunk being absorbed.
            body = chunk.content

            if chunk.header_path and body.startswith(chunk.header_path):
                body = body[len(chunk.header_path):].lstrip("\n")

            previous.content = f"{previous.content}\n\n{body}"
            previous.token_count = counter.count(previous.content)

            continue

        chunk.chunk_index = len(merged)
        merged.append(chunk)

    return merged
