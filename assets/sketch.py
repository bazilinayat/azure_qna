"""Shared drawing toolkit for the hand-drawn diagrams in assets/.

Everything renders at 2x and downsamples, which is what keeps the wobbly
outlines from looking jagged. Coordinates passed in are always in final-image
pixels; the scaling is internal.
"""

import math

from PIL import Image, ImageDraw, ImageFont

FONTS = "C:/Windows/Fonts/"

INK = FONTS + "Inkfree.ttf"
PRINT_R = FONTS + "segoepr.ttf"
PRINT_B = FONTS + "segoeprb.ttf"
MONO = FONTS + "consola.ttf"

# Saturated marker colours on white.
BLUE = (37, 99, 235)
PURPLE = (124, 58, 237)
GREEN = (22, 163, 74)
ORANGE = (234, 88, 12)
RED = (220, 38, 38)
AMBER = (217, 119, 6)
TEAL = (13, 148, 136)
PINK = (219, 39, 119)
INKC = (30, 32, 38)
GREY = (110, 116, 128)
FAINT = (205, 210, 218)


class Sketch:
    """A canvas with hand-drawn-looking primitives."""

    def __init__(self, width, height, scale=2, seed=7):
        import random

        self.W, self.H, self.S = width, height, scale
        self.rng = random.Random(seed)

        self.img = Image.new("RGB", (width * scale, height * scale), "white")
        self.d = ImageDraw.Draw(self.img)

    # -- helpers ---------------------------------------------------------

    def s(self, v):
        return int(v * self.S)

    def font(self, path, size):
        return ImageFont.truetype(path, int(size * self.S))

    def _jit(self, amount=1.3):
        return self.rng.uniform(-amount, amount) * self.S

    # -- primitives ------------------------------------------------------

    def rect(self, x, y, w, h, colour, r=12, width=3, passes=2, fill=None,
             dashed=False):
        """Rounded rectangle, drawn twice with a wobble for a marker look."""

        if fill:
            self.d.rounded_rectangle(
                [self.s(x), self.s(y), self.s(x + w), self.s(y + h)],
                radius=self.s(r), fill=fill,
            )

        for _ in range(1 if dashed else passes):
            self.d.rounded_rectangle(
                [
                    self.s(x) + self._jit(), self.s(y) + self._jit(),
                    self.s(x + w) + self._jit(), self.s(y + h) + self._jit(),
                ],
                radius=self.s(r), outline=colour, width=self.s(width),
            )

    def text(self, x, y, string, font, colour=INKC, anchor="la"):
        self.d.text((self.s(x), self.s(y)), string, font=font,
                    fill=colour, anchor=anchor)

    def textw(self, string, font):
        """Width of a string in final-image pixels."""

        return self.d.textlength(string, font=font) / self.S

    def wrap(self, string, font, max_px):
        words, lines, cur = string.split(), [], ""

        for word in words:
            trial = (cur + " " + word).strip()
            if self.textw(trial, font) <= max_px:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word

        if cur:
            lines.append(cur)

        return lines

    def line(self, x1, y1, x2, y2, colour, width=2):
        self.d.line([self.s(x1), self.s(y1), self.s(x2), self.s(y2)],
                    fill=colour, width=self.s(width))

    def dashed_line(self, x1, y1, x2, y2, colour, width=2, dash=7, gap=6):
        total = math.hypot(x2 - x1, y2 - y1)

        if total == 0:
            return

        ux, uy = (x2 - x1) / total, (y2 - y1) / total
        pos = 0.0

        while pos < total:
            end = min(pos + dash, total)
            self.line(x1 + ux * pos, y1 + uy * pos,
                      x1 + ux * end, y1 + uy * end, colour, width)
            pos = end + gap

    def arrowhead(self, x, y, angle, colour, size=9, width=2):
        for side in (0.5, -0.5):
            self.d.line(
                [
                    (self.s(x), self.s(y)),
                    (
                        self.s(x) - self.s(size) * math.cos(angle - side),
                        self.s(y) - self.s(size) * math.sin(angle - side),
                    ),
                ],
                fill=colour, width=self.s(width),
            )

    def arrow(self, x1, y1, x2, y2, colour, width=2, head=9, dashed=False):
        if dashed:
            self.dashed_line(x1, y1, x2, y2, colour, width)
        else:
            self.line(x1, y1, x2, y2, colour, width)

        self.arrowhead(x2, y2, math.atan2(y2 - y1, x2 - x1), colour, head, width)

    def curve(self, x1, y1, x2, y2, colour, width=3, head=10, bend=0.16):
        """Quadratic bezier with an arrowhead."""

        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        dx, dy = x2 - x1, y2 - y1
        cx, cy = mx - dy * bend, my + dx * bend

        pts = []
        for i in range(27):
            t = i / 26
            px = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t ** 2 * x2
            py = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t ** 2 * y2
            pts.append((self.s(px), self.s(py)))

        self.d.line(pts, fill=colour, width=self.s(width), joint="curve")

        (ex, ey), (px, py) = pts[-1], pts[-4]
        self.arrowhead(ex / self.S, ey / self.S,
                       math.atan2(ey - py, ex - px), colour, head, width)

    def self_arrow(self, x, y, colour, w=34, h=26, width=2):
        """A loop back to the same lifeline, for 'does something internally'."""

        self.line(x, y, x + w, y, colour, width)
        self.line(x + w, y, x + w, y + h, colour, width)
        self.arrow(x + w, y + h, x + 3, y + h, colour, width, head=8)

    def band(self, x, y, w, h, colour, label, font, r=16):
        """A faint container with a label in its top-left corner."""

        self.rect(x, y, w, h, colour, r=r, width=2, passes=1)
        tw = self.textw(label, font)

        self.d.rectangle(
            [self.s(x + 18), self.s(y - 10), self.s(x + 30 + tw), self.s(y + 12)],
            fill="white",
        )
        self.text(x + 24, y - 9, label, font, colour)

    # -- output ----------------------------------------------------------

    def save(self, path):
        import pathlib

        out = pathlib.Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)

        self.img.resize((self.W, self.H), Image.LANCZOS).save(
            out, "PNG", optimize=True
        )

        return out
