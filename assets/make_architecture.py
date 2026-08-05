"""Render the AzureMentor architecture poster as a PNG.

Hand-drawn infographic style: white background, rounded boxes with coloured
marker outlines, handwriting font, arrows into a central title block.
"""

import math
import pathlib
import random

from PIL import Image, ImageDraw, ImageFont

random.seed(7)

W, H = 1760, 1180
SCALE = 2  # supersample, then downscale for smooth edges

FONTS = "C:/Windows/Fonts/"

INK = FONTS + "Inkfree.ttf"
PRINT_R = FONTS + "segoepr.ttf"
PRINT_B = FONTS + "segoeprb.ttf"


def font(path, size):
    return ImageFont.truetype(path, size * SCALE)


F_TITLE = font(INK, 46)
F_SUB = font(INK, 21)
F_HEAD = font(PRINT_B, 18)
F_BODY = font(PRINT_R, 14)
F_SMALL = font(PRINT_R, 12)
F_TINY = font(PRINT_R, 11)
F_BANNER = font(INK, 26)
F_FLOW = font(PRINT_R, 12)

# Palette lifted from the reference: saturated marker colours on white.
BLUE = (37, 99, 235)
PURPLE = (124, 58, 237)
GREEN = (22, 163, 74)
ORANGE = (234, 88, 12)
RED = (220, 38, 38)
AMBER = (217, 119, 6)
TEAL = (13, 148, 136)
INKC = (30, 32, 38)
GREY = (110, 116, 128)

img = Image.new("RGB", (W * SCALE, H * SCALE), "white")
d = ImageDraw.Draw(img)


def s(v):
    return int(v * SCALE)


def jitter(amount=1.4):
    return random.uniform(-amount, amount) * SCALE


def sketch_round_rect(x, y, w, h, r, colour, width=3, passes=2, fill=None):
    """A rounded rectangle drawn with a slight wobble, twice, for a marker look."""

    if fill:
        d.rounded_rectangle(
            [s(x), s(y), s(x + w), s(y + h)], radius=s(r), fill=fill
        )

    for _ in range(passes):
        d.rounded_rectangle(
            [
                s(x) + jitter(), s(y) + jitter(),
                s(x + w) + jitter(), s(y + h) + jitter(),
            ],
            radius=s(r),
            outline=colour,
            width=s(width) // SCALE * SCALE // 1 or 1,
        )


def text(x, y, string, fnt, colour=INKC, anchor="la"):
    d.text((s(x), s(y)), string, font=fnt, fill=colour, anchor=anchor)


def wrap(string, fnt, max_px):
    """Greedy wrap on words, measured in real pixels."""

    words, lines, cur = string.split(), [], ""

    for word in words:
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=fnt) <= max_px * SCALE:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word

    if cur:
        lines.append(cur)

    return lines


def bullets(x, y, items, colour, fnt=F_BODY, marker="dot", width=300, gap=21):
    """Bulleted lines with wrapping. Returns the y after the last line."""

    cy = y

    for item in items:
        if marker == "check":
            d.line([s(x), s(cy + 8), s(x + 4), s(cy + 12)], fill=colour, width=s(2))
            d.line([s(x + 4), s(cy + 12), s(x + 11), s(cy + 2)], fill=colour, width=s(2))
        else:
            d.ellipse(
                [s(x + 1), s(cy + 5), s(x + 7), s(cy + 11)], fill=colour
            )

        for i, line in enumerate(wrap(item, fnt, width)):
            text(x + 17, cy + i * 17, line, fnt)

        cy += gap + 17 * (len(wrap(item, fnt, width)) - 1)

    return cy


def section(x, y, w, h, number, title, colour, items, marker="dot",
            fnt=F_BODY, gap=21):
    sketch_round_rect(x, y, w, h, 14, colour)
    text(x + 18, y + 13, f"{number}. {title}", F_HEAD, colour)
    bullets(x + 20, y + 45, items, colour, fnt=fnt, marker=marker,
            width=w - 46, gap=gap)


def arrow(x1, y1, x2, y2, colour, width=3, head=11, curve=0.16):
    """A gently curved arrow, approximated with a quadratic bezier."""

    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    dx, dy = x2 - x1, y2 - y1
    cx, cy = mx - dy * curve, my + dx * curve

    pts = []
    steps = 26
    for i in range(steps + 1):
        t = i / steps
        px = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t ** 2 * x2
        py = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t ** 2 * y2
        pts.append((s(px), s(py)))

    d.line(pts, fill=colour, width=s(width), joint="curve")

    ex, ey = pts[-1]
    px, py = pts[-4]
    ang = math.atan2(ey - py, ex - px)

    for side in (2.6, -2.6):
        d.line(
            [
                (ex, ey),
                (
                    ex - s(head) * math.cos(ang - side / 2.6 * 0.5),
                    ey - s(head) * math.sin(ang - side / 2.6 * 0.5),
                ),
            ],
            fill=colour,
            width=s(width),
        )


def straight_arrow(x1, y1, x2, y2, colour, width=2, head=7):
    d.line([s(x1), s(y1), s(x2), s(y2)], fill=colour, width=s(width))
    ang = math.atan2(y2 - y1, x2 - x1)
    for side in (0.5, -0.5):
        d.line(
            [
                (s(x2), s(y2)),
                (
                    s(x2) - s(head) * math.cos(ang - side),
                    s(y2) - s(head) * math.sin(ang - side),
                ),
            ],
            fill=colour,
            width=s(width),
        )


# ----------------------------------------------------------------------
# Header banner
# ----------------------------------------------------------------------

text(W / 2, 22, "AZUREMENTOR  ARCHITECTURE", F_BANNER, GREEN, anchor="ma")
d.line(
    [s(W / 2 - 250), s(58), s(W / 2 + 250), s(58)], fill=GREEN, width=s(3)
)

# ----------------------------------------------------------------------
# Top workflow strip
# ----------------------------------------------------------------------

FLOW_Y = 92
flow = [
    ("User asks", "a question"),
    ("Streamlit", "chat UI"),
    ("BM25  +", "vectors"),
    ("RRF fuses", "rankings"),
    ("gpt-5.4-mini", "grounded"),
    ("Answer with", "[1] citations"),
    ("Logged to", "Grafana"),
]

flow_colours = [BLUE, PURPLE, TEAL, TEAL, AMBER, GREEN, RED]
fx = 512
step = 108

for i, (a, b) in enumerate(flow):
    cx = fx + i * step
    d.ellipse(
        [s(cx - 20), s(FLOW_Y - 20), s(cx + 20), s(FLOW_Y + 20)],
        outline=flow_colours[i],
        width=s(3),
    )
    text(cx, FLOW_Y + 26, a, F_FLOW, INKC, anchor="ma")
    text(cx, FLOW_Y + 40, b, F_FLOW, GREY, anchor="ma")

    if i < len(flow) - 1:
        straight_arrow(cx + 24, FLOW_Y, cx + step - 25, FLOW_Y,
                       flow_colours[i], width=2, head=7)

# Little glyphs inside the flow circles.
def glyph_person(cx, cy, colour):
    d.ellipse([s(cx - 5), s(cy - 11), s(cx + 5), s(cy - 1)], outline=colour, width=s(2))
    d.arc([s(cx - 9), s(cy - 1), s(cx + 9), s(cy + 15)], 180, 360, fill=colour, width=s(2))


def glyph_chat(cx, cy, colour):
    d.rounded_rectangle([s(cx - 11), s(cy - 9), s(cx + 11), s(cy + 5)],
                        radius=s(5), outline=colour, width=s(2))
    d.line([s(cx - 4), s(cy + 5), s(cx - 7), s(cy + 11)], fill=colour, width=s(2))


def glyph_search(cx, cy, colour):
    d.ellipse([s(cx - 11), s(cy - 11), s(cx + 3), s(cy + 3)], outline=colour, width=s(2))
    d.line([s(cx + 2), s(cy + 2), s(cx + 10), s(cy + 10)], fill=colour, width=s(3))


def glyph_merge(cx, cy, colour):
    d.line([s(cx - 10), s(cy - 9), s(cx + 2), s(cy)], fill=colour, width=s(2))
    d.line([s(cx - 10), s(cy + 9), s(cx + 2), s(cy)], fill=colour, width=s(2))
    d.line([s(cx + 2), s(cy), s(cx + 11), s(cy)], fill=colour, width=s(2))


def glyph_brain(cx, cy, colour):
    d.ellipse([s(cx - 11), s(cy - 10), s(cx + 1), s(cy + 4)], outline=colour, width=s(2))
    d.ellipse([s(cx - 2), s(cy - 7), s(cx + 11), s(cy + 8)], outline=colour, width=s(2))


def glyph_doc(cx, cy, colour):
    d.rounded_rectangle([s(cx - 9), s(cy - 12), s(cx + 9), s(cy + 11)],
                        radius=s(3), outline=colour, width=s(2))
    for k in range(3):
        d.line([s(cx - 5), s(cy - 5 + k * 6), s(cx + 5), s(cy - 5 + k * 6)],
               fill=colour, width=s(2))


def glyph_chart(cx, cy, colour):
    for k, hgt in enumerate((6, 12, 9)):
        d.rectangle(
            [s(cx - 9 + k * 7), s(cy + 8 - hgt), s(cx - 4 + k * 7), s(cy + 8)],
            outline=colour, width=s(2),
        )


for i, fn in enumerate([glyph_person, glyph_chat, glyph_search, glyph_merge,
                        glyph_brain, glyph_doc, glyph_chart]):
    fn(fx + i * step, FLOW_Y, flow_colours[i])

# ----------------------------------------------------------------------
# Centre block
# ----------------------------------------------------------------------

CX, CY, CW, CH = 545, 372, 500, 210

sketch_round_rect(CX, CY, CW, CH, 22, INKC, width=4, passes=2)
text(CX + CW / 2, CY + 34, "AZUREMENTOR", F_TITLE, INKC, anchor="ma")
text(CX + CW / 2, CY + 100, "RAG over 31,736 chunks", F_SUB, PURPLE, anchor="ma")
text(CX + CW / 2, CY + 130, "of official Azure documentation", F_SUB, PURPLE, anchor="ma")
text(CX + CW / 2, CY + 168, "Ask  ->  Retrieve  ->  Cite", F_SUB, GREY, anchor="ma")

# ----------------------------------------------------------------------
# Left column
# ----------------------------------------------------------------------

section(
    28, 176, 380, 198, "1", "WHY THIS EXISTS", BLUE,
    [
        "13,500 Azure articles, organised by service",
        "Learners ask by task, not by service name",
        "Chatbots answer confidently, cite nothing",
        "Every answer here links back to Learn",
    ],
    marker="check",
)

section(
    28, 392, 380, 210, "2", "WHAT GETS INDEXED", PURPLE,
    [
        "MicrosoftDocs/azure-docs, CC-BY-4.0",
        "15 core services, ~3,850 articles",
        "Header-aware chunks, 480 model tokens",
        "Code blocks and tables kept intact",
        "Markdown-only clone: 7.6 GB -> 252 MB",
    ],
)

section(
    28, 620, 380, 196, "5", "EVALUATION", GREEN,
    [
        "450 synthetic ground-truth questions",
        "Hit rate 0.933   MRR 0.835",
        "Hybrid beats BM25 (0.765) and vectors (0.767)",
        "LLM-as-judge scores every answer",
    ],
    marker="check",
)

section(
    28, 834, 380, 200, "6", "GOTCHAS I HIT", ORANGE,
    [
        "tiktoken != model tokens -> silent truncation",
        "fastembed 4x slower than plain torch",
        "CUDA wheels blew up the Docker build",
        "WAL mode breaks Grafana on Windows",
    ],
)

# ----------------------------------------------------------------------
# Right column
# ----------------------------------------------------------------------

section(
    1352, 176, 380, 210, "3", "HOW RETRIEVAL WORKS", RED,
    [
        "BM25 over SQLite FTS5 index",
        "Dense vectors in Qdrant (bge-small)",
        "Fused with Reciprocal Rank Fusion",
        "Rerank + expansion measured, then OFF",
        "0.27s per query, warm",
    ],
)

section(
    1352, 404, 380, 196, "4", "HOW ANSWERS ARE MADE", AMBER,
    [
        "Top 5 chunks, numbered as context",
        "gpt-5.4-mini with a grounded prompt",
        "Cites [1] [2] inline, per claim",
        "No retrieval -> the LLM is never called",
    ],
)

section(
    1352, 618, 380, 198, "7", "MONITORING", TEAL,
    [
        "Every answer logged to its own SQLite db",
        "Tokens, cost, latency, relevance",
        "Thumbs up/down from real users",
        "13 Grafana panels, provisioned",
    ],
    marker="check",
)

# ----------------------------------------------------------------------
# Tools strip (right, bottom)
# ----------------------------------------------------------------------

sketch_round_rect(1064, 834, 668, 200, 14, BLUE)
text(1398, 848, "THE STACK", F_HEAD, BLUE, anchor="ma")

tools = [
    ("Streamlit", "chat UI"),
    ("SQLite", "docs + BM25"),
    ("Qdrant", "vectors"),
    ("OpenAI", "answers"),
    ("Grafana", "dashboards"),
    ("Docker", "all of it"),
]

tw = 668 / len(tools)
for i, (name, what) in enumerate(tools):
    tx = 1064 + tw * (i + 0.5)
    text(tx, 890, name, F_BODY, INKC, anchor="ma")
    text(tx, 912, what, F_SMALL, GREY, anchor="ma")

    if i:
        d.line([s(1064 + tw * i), s(884), s(1064 + tw * i), s(932)],
               fill=(210, 214, 220), width=s(2))

text(1398, 958, "One command:  docker compose --profile app up -d", F_BODY,
     INKC, anchor="ma")
text(1398, 984, "Index rebuild is a second, separate command", F_SMALL,
     GREY, anchor="ma")

# ----------------------------------------------------------------------
# Key takeaway (centre, below the block)
# ----------------------------------------------------------------------

sketch_round_rect(545, 616, 500, 176, 14, BLUE)
text(795, 630, "KEY TAKEAWAY", F_HEAD, BLUE, anchor="ma")

for i, line in enumerate([
    "Answers are grounded in real documentation",
    "and carry citations you can open and check.",
    "Every design choice here was measured,",
    "not assumed -- and several measurements",
    "overturned what seemed obvious.",
]):
    text(795, 660 + i * 24, line, F_BODY, INKC, anchor="ma")

# A small star, as in the reference.
def star(cx, cy, r, colour):
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.45
        pts.append((s(cx + rad * math.cos(ang)), s(cy - rad * math.sin(ang))))
    d.polygon(pts, fill=colour)


star(516, 628, 17, BLUE)

# ----------------------------------------------------------------------
# Principles strip (centre bottom)
# ----------------------------------------------------------------------

sketch_round_rect(430, 834, 610, 200, 14, PURPLE)
text(735, 848, "PRINCIPLES THAT SHAPED IT", F_HEAD, PURPLE, anchor="ma")

principles = [
    ("Measure,", "don't assume"),
    ("Fail closed,", "never guess"),
    ("One knob,", "not five"),
    ("Verify every", "claim"),
]

def glyph_ruler(cx, cy, colour):
    for k, hgt in enumerate((5, 10, 7)):
        d.rectangle([s(cx - 8 + k * 6), s(cy + 7 - hgt), s(cx - 4 + k * 6), s(cy + 7)],
                    outline=colour, width=s(2))


def glyph_shield(cx, cy, colour):
    d.polygon([(s(cx), s(cy - 9)), (s(cx + 8), s(cy - 5)), (s(cx + 8), s(cy + 2)),
               (s(cx), s(cy + 9)), (s(cx - 8), s(cy + 2)), (s(cx - 8), s(cy - 5))],
              outline=colour)
    d.line([s(cx - 4), s(cy), s(cx - 1), s(cy + 3)], fill=colour, width=s(2))
    d.line([s(cx - 1), s(cy + 3), s(cx + 4), s(cy - 4)], fill=colour, width=s(2))


def glyph_knob(cx, cy, colour):
    d.ellipse([s(cx - 9), s(cy - 9), s(cx + 9), s(cy + 9)], outline=colour, width=s(2))
    d.line([s(cx), s(cy), s(cx + 5), s(cy - 6)], fill=colour, width=s(2))


def glyph_tick(cx, cy, colour):
    d.ellipse([s(cx - 9), s(cy - 9), s(cx + 5), s(cy + 5)], outline=colour, width=s(2))
    d.line([s(cx + 4), s(cy + 4), s(cx + 9), s(cy + 9)], fill=colour, width=s(2))
    d.line([s(cx - 5), s(cy - 2), s(cx - 2), s(cy + 1)], fill=colour, width=s(2))
    d.line([s(cx - 2), s(cy + 1), s(cx + 2), s(cy - 5)], fill=colour, width=s(2))


principle_glyphs = [glyph_ruler, glyph_shield, glyph_knob, glyph_tick]

pw = 610 / len(principles)
for i, (a, b) in enumerate(principles):
    px = 430 + pw * (i + 0.5)
    d.ellipse([s(px - 17), s(885), s(px + 17), s(919)], outline=PURPLE, width=s(2))
    principle_glyphs[i](px, 902, PURPLE)
    text(px, 934, a, F_SMALL, INKC, anchor="ma")
    text(px, 952, b, F_SMALL, INKC, anchor="ma")

text(735, 990, "Reranking looked essential. The data said otherwise.",
     F_SMALL, GREY, anchor="ma")

# ----------------------------------------------------------------------
# Arrows into the centre
# ----------------------------------------------------------------------

arrow(408, 250, 540, 372, BLUE, curve=0.10)
arrow(408, 470, 540, 452, PURPLE, curve=0.05)
arrow(408, 690, 545, 540, GREEN, curve=-0.10)
arrow(1352, 262, 1052, 388, RED, curve=0.10)
arrow(1352, 484, 1052, 466, AMBER, curve=-0.05)
arrow(1352, 700, 1052, 545, TEAL, curve=-0.08)

# Workflow strip down into the centre block.
straight_arrow(795, 150, 795, 366, GREEN, width=3, head=10)

# Centre block down into the takeaway.
straight_arrow(795, 584, 795, 612, BLUE, width=2, head=8)

# ----------------------------------------------------------------------
# Footer pills
# ----------------------------------------------------------------------

sketch_round_rect(28, 1060, 700, 56, 26, PURPLE, width=2)
text(378, 1075, "Ingest  ->  Chunk  ->  Embed  ->  Retrieve  ->  Cite  ->  Measure",
     F_BODY, PURPLE, anchor="ma")

sketch_round_rect(1032, 1060, 700, 56, 26, GREEN, width=2)
text(1382, 1075, "Grounded answers  =  answers you can actually trust",
     F_BODY, GREEN, anchor="ma")

# ----------------------------------------------------------------------

out = pathlib.Path("assets/architecture.png")
out.parent.mkdir(parents=True, exist_ok=True)

img = img.resize((W, H), Image.LANCZOS)
img.save(out, "PNG", optimize=True)

print(f"wrote {out}  {img.size[0]}x{img.size[1]}  {out.stat().st_size/1024:.0f} KB")
