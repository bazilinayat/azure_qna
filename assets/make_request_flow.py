"""Render the per-request sequence as a PNG.

The picture equivalent of the sequence diagram in ARCHITECTURE.md: what talks to
what, in what order, when someone asks a question.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sketch import (  # noqa: E402
    AMBER, BLUE, FAINT, GREEN, GREY, INK, INKC, MONO, PINK, PRINT_B, PRINT_R,
    PURPLE, RED, TEAL, Sketch,
)

sk = Sketch(1700, 1180, seed=5)

F_TITLE = sk.font(INK, 34)
F_ACTOR = sk.font(PRINT_B, 14)
F_ACTOR_SUB = sk.font(MONO, 10)
F_MSG = sk.font(PRINT_R, 13)
F_NOTE = sk.font(PRINT_R, 13)
F_TAG = sk.font(PRINT_R, 11)
F_BAND = sk.font(PRINT_B, 13)

# ----------------------------------------------------------------------
# Participants
# ----------------------------------------------------------------------

actors = [
    ("User", "", GREY),
    ("Streamlit", "ui/streamlit_app.py", PURPLE),
    ("HybridSearch", "search/hybrid_search.py", TEAL),
    ("FTS5", "SQLite BM25", BLUE),
    ("Qdrant", "vectors", BLUE),
    ("OpenAI", "llm/client.py", AMBER),
    ("monitoring.db", "monitoring/store.py", PINK),
]

LEFT, RIGHT = 110, 1600
TOP = 118
BOTTOM = 1046

lane = [LEFT + (RIGHT - LEFT) * i / (len(actors) - 1) for i in range(len(actors))]

sk.text(850, 20, "WHAT HAPPENS WHEN YOU ASK A QUESTION", F_TITLE, INKC, anchor="ma")
sk.line(560, 62, 1140, 62, TEAL, width=3)

for (name, sub, colour), x in zip(actors, lane):
    w = max(120, sk.textw(name, F_ACTOR) + 30, sk.textw(sub, F_ACTOR_SUB) + 24)

    sk.rect(x - w / 2, TOP - 46, w, 52, colour, r=10, width=3)
    sk.text(x, TOP - 40, name, F_ACTOR, INKC, anchor="ma")

    if sub:
        sk.text(x, TOP - 20, sub, F_ACTOR_SUB, colour, anchor="ma")

    sk.dashed_line(x, TOP + 8, x, BOTTOM, FAINT, width=2, dash=6, gap=7)

USER, APP, HYB, FTS, QDR, LLM, MON = lane


def msg(y, x1, x2, label, colour, dashed=False, above=True, tag=None):
    """A horizontal message between two lifelines."""

    sk.arrow(x1, y, x2, y, colour, width=2, head=9, dashed=dashed)

    mid = (x1 + x2) / 2
    sk.text(mid, y - 20 if above else y + 6, label, F_MSG, INKC, anchor="ma")

    if tag:
        sk.text(mid, y - 36 if above else y + 22, tag, F_TAG, GREY, anchor="ma")


def selfmsg(y, x, label, colour):
    sk.self_arrow(x, y, colour, w=40, h=30, width=2)
    sk.text(x + 52, y + 20, label, F_MSG, INKC)


# ----------------------------------------------------------------------
# Messages
# ----------------------------------------------------------------------

y = 168

msg(y, USER, APP, '"how do I secure a blob container"', GREY)

y += 62
msg(y, APP, HYB, "search(question)", PURPLE)

# -- the parallel retrieval block --
# The four messages are laid out first so the enclosing box can be sized from
# where they actually landed; guessing its height left the last return arrow
# hanging outside it.
PAR_TOP = y + 30

y += 74
bm25_out = y

y += 46
bm25_in = y

y += 52
vec_out = y

y += 46
vec_in = y

PAR_BOTTOM = vec_in + 30

sk.rect(HYB - 70, PAR_TOP, QDR - HYB + 190, PAR_BOTTOM - PAR_TOP, TEAL,
        r=12, width=2, passes=1)

label = "PARALLEL  -  both retrievers at once"
sk.d.rectangle(
    [sk.s(HYB - 52), sk.s(PAR_TOP - 10), sk.s(HYB - 40 + sk.textw(label, F_BAND)),
     sk.s(PAR_TOP + 12)],
    fill="white",
)
sk.text(HYB - 46, PAR_TOP - 9, label, F_BAND, TEAL)

msg(bm25_out, HYB, FTS, "BM25, 30 candidates", TEAL)
msg(bm25_in, FTS, HYB, "chunks + bm25 scores", BLUE, dashed=True, above=False)
msg(vec_out, HYB, QDR, "vector search, 30 candidates", TEAL)
msg(vec_in, QDR, HYB, "chunks + cosine scores", BLUE, dashed=True, above=False)

# -- fusion --
y += 68
selfmsg(y, HYB, "RRF fuses both rankings   (search/rrf.py)", TEAL)

y += 74
msg(y, HYB, APP, "top 5 chunks", TEAL, dashed=True)

y += 62
msg(y, APP, LLM, "system prompt + numbered context + question", AMBER)

y += 56
msg(y, LLM, APP, "answer citing [1] [2]", AMBER, dashed=True, above=False)

y += 62
msg(y, APP, USER, "render answer + only the cited sources", GREEN)

y += 66
msg(y, APP, LLM, "judge relevance", PINK,
    tag="second call, AFTER the answer is on screen")

y += 56
msg(y, APP, MON, "tokens, cost, latency, relevance", PINK, dashed=True, above=False)

y += 62
msg(y, USER, MON, "thumbs up / down", RED)

# ----------------------------------------------------------------------
# Notes
# ----------------------------------------------------------------------

sk.rect(60, 1076, 780, 84, GREEN, r=16, width=2, passes=1)
sk.text(80, 1090, "Nothing retrieved?  The LLM is never called.", F_NOTE, GREEN)
sk.text(80, 1114, "Answering with no context is exactly the confident,", F_NOTE, INKC)
sk.text(80, 1136, "uncited guess this project exists to prevent.", F_NOTE, INKC)

sk.rect(880, 1076, 760, 84, PINK, r=16, width=2, passes=1)
sk.text(900, 1090, "The judge runs after rendering.", F_NOTE, PINK)
sk.text(900, 1114, "The user never waits on a call made for our benefit --", F_NOTE, INKC)
sk.text(900, 1136, "it costs a second request, so JUDGE_LIVE_ANSWERS turns it off.", F_NOTE, INKC)

out = sk.save("assets/request-flow.png")
print(f"wrote {out}  {sk.W}x{sk.H}  {out.stat().st_size / 1024:.0f} KB")
