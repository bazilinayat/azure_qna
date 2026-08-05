"""Render the build/query/observe pipeline as a PNG.

The picture equivalent of the first Mermaid diagram in ARCHITECTURE.md, with the
module that implements each step named on the box.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sketch import (  # noqa: E402
    AMBER, BLUE, FAINT, GREEN, GREY, INK, INKC, MONO, PINK, PRINT_B, PRINT_R,
    PURPLE, RED, TEAL, Sketch,
)

sk = Sketch(1760, 1160, seed=11)

F_TITLE = sk.font(INK, 34)
F_BAND = sk.font(PRINT_B, 17)
F_BOX = sk.font(PRINT_B, 14)
F_SUB = sk.font(PRINT_R, 12)
F_FILE = sk.font(MONO, 11)
F_NOTE = sk.font(PRINT_R, 13)
F_TAG = sk.font(PRINT_R, 11)


def node(x, y, w, h, title, subtitle, filename, colour, r=12, dashed=False):
    """A step in the pipeline: what it does, plus the file that does it."""

    sk.rect(x, y, w, h, colour, r=r, width=3, passes=1 if dashed else 2)

    cx = x + w / 2
    sk.text(cx, y + 12, title, F_BOX, INKC, anchor="ma")

    if subtitle:
        sk.text(cx, y + 32, subtitle, F_SUB, GREY, anchor="ma")

    if filename:
        sk.text(cx, y + h - 22, filename, F_FILE, colour, anchor="ma")


def store(x, y, w, h, title, subtitle, colour):
    """A datastore: cylinder-ish, drawn as a rect with an elliptical cap."""

    sk.d.ellipse(
        [sk.s(x), sk.s(y - 9), sk.s(x + w), sk.s(y + 15)],
        outline=colour, width=sk.s(3),
    )
    sk.d.line([sk.s(x), sk.s(y + 3), sk.s(x), sk.s(y + h)],
              fill=colour, width=sk.s(3))
    sk.d.line([sk.s(x + w), sk.s(y + 3), sk.s(x + w), sk.s(y + h)],
              fill=colour, width=sk.s(3))
    sk.d.arc([sk.s(x), sk.s(y + h - 14), sk.s(x + w), sk.s(y + h + 12)],
             0, 180, fill=colour, width=sk.s(3))

    cx = x + w / 2
    sk.text(cx, y + 26, title, F_BOX, INKC, anchor="ma")
    sk.text(cx, y + 46, subtitle, F_SUB, GREY, anchor="ma")


# ----------------------------------------------------------------------
# Title
# ----------------------------------------------------------------------

sk.text(880, 20, "HOW THE PIPELINE FITS TOGETHER", F_TITLE, INKC, anchor="ma")
sk.line(630, 62, 1130, 62, GREEN, width=3)

# ----------------------------------------------------------------------
# BUILD TIME
# ----------------------------------------------------------------------

sk.band(40, 108, 1680, 262, PURPLE, "BUILD TIME  -  run once, about an hour", F_BAND)

sk.text(60, 128, "app/pipeline.py --fresh", F_FILE, PURPLE)

BY = 176

node(72, BY, 216, 116, "azure-docs", "markdown-only clone", "252 MB", PURPLE)
node(336, BY, 216, 116, "Clean", "strip Learn directives", "ingest/markdown.py", PURPLE)
node(600, BY, 216, 116, "Chunk", "header-aware, 480 tok", "ingest/chunker.py", PURPLE)

store(880, BY + 4, 216, 108, "SQLite", "documents + chunks", BLUE)

node(1176, BY - 44, 216, 104, "BM25 index", "FTS5, porter stems", "db/fts.py", TEAL)
node(1176, BY + 84, 216, 104, "Embed", "bge-small, torch", "embedding/index.py", TEAL)

store(1456, BY + 88, 216, 100, "Qdrant", "31,736 vectors", TEAL)

sk.arrow(288, BY + 58, 330, BY + 58, PURPLE, width=3, head=10)
sk.arrow(552, BY + 58, 594, BY + 58, PURPLE, width=3, head=10)
sk.arrow(816, BY + 58, 874, BY + 58, PURPLE, width=3, head=10)

sk.arrow(1096, BY + 40, 1170, BY + 8, BLUE, width=3, head=10)
sk.arrow(1096, BY + 76, 1170, BY + 136, BLUE, width=3, head=10)
sk.arrow(1392, BY + 136, 1450, BY + 136, TEAL, width=3, head=10)

# ----------------------------------------------------------------------
# QUERY TIME
# ----------------------------------------------------------------------

sk.band(40, 420, 1680, 300, GREEN, "QUERY TIME  -  every question, about 5 seconds", F_BAND)

QY = 500

node(72, QY, 200, 112, "Question", "from the chat UI", "ui/streamlit_app.py", GREEN)

node(322, QY - 66, 216, 104, "BM25 search", "top 30 from FTS5", "search/keyword_search.py", TEAL)
node(322, QY + 62, 216, 104, "Vector search", "top 30 from Qdrant", "search/vector_search.py", TEAL)

node(586, QY, 190, 112, "RRF fusion", "rank-based merge", "search/rrf.py", GREEN)

node(824, QY, 200, 112, "Build prompt", "5 chunks, numbered", "llm/prompts.py", AMBER)
node(1072, QY, 200, 112, "OpenAI", "gpt-5.4-mini", "llm/client.py", AMBER)
node(1320, QY, 200, 112, "Answer", "cites [1] [2]", "llm/rag.py", GREEN)

sk.arrow(272, QY + 34, 316, QY - 6, TEAL, width=3, head=10)
sk.arrow(272, QY + 78, 316, QY + 116, TEAL, width=3, head=10)
sk.arrow(538, QY - 6, 582, QY + 34, TEAL, width=3, head=10)
sk.arrow(538, QY + 116, 582, QY + 78, TEAL, width=3, head=10)
sk.arrow(776, QY + 56, 818, QY + 56, GREEN, width=3, head=10)
sk.arrow(1024, QY + 56, 1066, QY + 56, AMBER, width=3, head=10)
sk.arrow(1272, QY + 56, 1314, QY + 56, AMBER, width=3, head=10)

# The two stages that were measured and switched off.
sk.rect(586, QY + 150, 190, 62, GREY, r=12, width=2, passes=1)
sk.text(681, QY + 163, "expansion + rerank", F_TAG, GREY, anchor="ma")
sk.text(681, QY + 182, "measured, then OFF", F_TAG, GREY, anchor="ma")
sk.dashed_line(681, QY + 112, 681, QY + 148, GREY, width=2)

# The dependency between the bands is stated rather than drawn: the two search
# boxes already name their source, and long connector lines across the whole
# image obscured more than they explained.
sk.text(880, 388, "query time only ever reads the index built above -- "
                  "it never touches the docs repository",
        F_TAG, GREY, anchor="ma")

# ----------------------------------------------------------------------
# OBSERVABILITY
# ----------------------------------------------------------------------

sk.band(40, 772, 1680, 236, RED, "OBSERVABILITY  -  after every answer", F_BAND)

OY = 838

node(180, OY, 216, 112, "LLM judge", "relevance verdict", "eval/judge.py", PINK)
node(500, OY, 216, 112, "Log the turn", "tokens, cost, latency", "monitoring/store.py", PINK)

store(820, OY + 4, 216, 104, "monitoring.db", "its own database", BLUE)

node(1160, OY, 216, 112, "Grafana", "13 provisioned panels", "grafana/", RED)
node(1440, OY, 220, 112, "Thumbs up/down", "from real users", "ui/streamlit_app.py", RED)

sk.arrow(396, OY + 56, 494, OY + 56, PINK, width=3, head=10)
sk.arrow(716, OY + 56, 814, OY + 56, PINK, width=3, head=10)
sk.arrow(1036, OY + 56, 1154, OY + 56, BLUE, width=3, head=10)
sk.arrow(1440, OY + 90, 1382, OY + 90, RED, width=3, head=10)

# Answer -> judge, routed orthogonally down the right and back along the gap
# between the bands. A straight diagonal here crossed the whole diagram.
sk.line(1420, QY + 112, 1420, 746, PINK, width=3)
sk.line(1420, 746, 288, 746, PINK, width=3)
sk.arrow(288, 746, 288, OY - 6, PINK, width=3, head=10)

sk.text(854, 730, "every answer is judged and logged", F_TAG, PINK, anchor="ma")

# ----------------------------------------------------------------------
# Footnotes
# ----------------------------------------------------------------------

sk.rect(40, 1044, 1680, 74, GREY, r=20, width=2, passes=1)

sk.text(60, 1058,
        "Build time and query time never touch each other. The pipeline writes "
        "SQLite and Qdrant; the app only reads them --", F_NOTE, INKC)
sk.text(60, 1082,
        "which is why a retrieval setting changes instantly, and a chunking "
        "setting costs a rebuild. Monitoring is a third, separate database so "
        "--fresh cannot wipe it.", F_NOTE, INKC)

out = sk.save("assets/pipeline.png")
print(f"wrote {out}  {sk.W}x{sk.H}  {out.stat().st_size / 1024:.0f} KB")
