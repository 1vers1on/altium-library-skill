"""An RGB drawing canvas with a PNG encoder -- the bundled ``Pillow`` replacement.

Only what the QA previews need: filled and outlined polygons, polylines, ellipses,
axis-aligned rectangles, stroke-font text, compositing one canvas into another, and an
8-bit truecolour PNG written with :mod:`zlib` from the standard library.

Shapes are anti-aliased by sampling four sub-scanlines per pixel row and computing exact
horizontal coverage on each, which keeps thin schematic strokes and small text legible
without a per-pixel supersampled buffer. Fully covered runs are written with a slice
assignment, so large fills stay fast in pure Python.
"""

import math
import struct
import zlib
from pathlib import Path
from . import strokefont

SUBSAMPLES = 4


def rgb(color):
    """Accept ``'#rgb'``, ``'#rrggbb'``, an ``(r, g, b)`` triple, or an already packed triple."""
    if isinstance(color, (bytes, bytearray)) and len(color) == 3:
        return bytes(color)
    if isinstance(color, (tuple, list)):
        r, g, b = (int(c) for c in color)
    else:
        text = str(color).lstrip("#")
        if len(text) == 3:
            text = "".join(c * 2 for c in text)
        if len(text) != 6:
            raise ValueError(f"Not a colour: {color!r}")
        r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    for c in (r, g, b):
        if not 0 <= c <= 255:
            raise ValueError(f"Colour component out of range: {color!r}")
    return bytes((r, g, b))


class Canvas:
    """A fixed-size RGB image."""

    def __init__(self, width, height, background="#ffffff"):
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.stride = self.width * 3
        self.buffer = bytearray(rgb(background) * (self.width * self.height))

    # -- primitives ------------------------------------------------------------------------

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, width=1.0):
        """Axis-aligned rectangle. Whole-pixel fill, so large bodies cost one slice per row."""
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        if fill is not None:
            color = rgb(fill)
            a = max(0, int(round(x0)))
            b = min(self.width, int(round(x1)))
            top = max(0, int(round(y0)))
            bottom = min(self.height, int(round(y1)))
            if b > a:
                row = color * (b - a)
                for y in range(top, bottom):
                    start = y * self.stride + a * 3
                    self.buffer[start : start + len(row)] = row
        if outline is not None:
            self.line(
                [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
                outline,
                width,
            )

    def polygon(self, points, fill=None, outline=None, width=1.0):
        points = [(float(x), float(y)) for x, y in points]
        if fill is not None and len(points) >= 3:
            self._fill(points, rgb(fill))
        if outline is not None and points:
            self.line(points + [points[0]], outline, width)

    def line(self, points, color, width=1.0):
        """A polyline drawn with square-extended caps, so joins close without extra work."""
        points = [(float(x), float(y)) for x, y in points]
        if len(points) < 2:
            if points:
                self.dot(points[0][0], points[0][1], max(0.5, width / 2), color)
            return
        paint = rgb(color)
        half = max(0.35, width / 2.0)
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy)
            if length < 1e-9:
                self.dot(ax, ay, half, color)
                continue
            ux, uy = dx / length, dy / length
            px, py = -uy * half, ux * half
            ex, ey = ux * half * 0.5, uy * half * 0.5
            self._fill(
                [
                    (ax - ex + px, ay - ey + py),
                    (bx + ex + px, by + ey + py),
                    (bx + ex - px, by + ey - py),
                    (ax - ex - px, ay - ey - py),
                ],
                paint,
            )

    def dot(self, x, y, radius, color):
        self.ellipse(x, y, radius, radius, fill=color)

    def ellipse(self, cx, cy, rx, ry, fill=None, outline=None, width=1.0):
        steps = max(8, int(2 * math.pi * max(rx, ry) / 2))
        points = [
            (
                cx + rx * math.cos(2 * math.pi * i / steps),
                cy + ry * math.sin(2 * math.pi * i / steps),
            )
            for i in range(steps)
        ]
        self.polygon(points, fill=fill, outline=outline, width=width)

    def paste(self, other, x, y):
        """Copy ``other`` in with its top-left corner at ``(x, y)``, clipped to this canvas."""
        x = int(x)
        y = int(y)
        for row in range(other.height):
            dy = y + row
            if not 0 <= dy < self.height:
                continue
            sx = max(0, -x)
            ex = min(other.width, self.width - x)
            if ex <= sx:
                continue
            src = row * other.stride + sx * 3
            dst = dy * self.stride + (x + sx) * 3
            self.buffer[dst : dst + (ex - sx) * 3] = other.buffer[
                src : src + (ex - sx) * 3
            ]

    # -- text ------------------------------------------------------------------------------

    def text(
        self,
        x,
        y,
        string,
        size,
        advances,
        anchor="ls",
        fill="#000000",
        ink=None,
        weight=1.0,
    ):
        """Draw ``string`` with the bundled stroke font.

        ``size`` is the em height in pixels and ``advances`` the per-character advance in
        pixels, both supplied by the caller so the picture matches the metrics the layout
        was fitted with. ``anchor`` is a two-letter Pillow-style code: horizontal ``l``/
        ``m``/``r``, vertical ``s`` (baseline) or ``m`` (ink box centred on ``y``).
        """
        string = str(string)
        if not string:
            return
        advances = list(advances)
        if len(advances) != len(string):
            raise ValueError("One advance is required per character.")
        total = sum(advances)
        ink = 0.72 * size if ink is None else ink
        pen = {"l": x, "m": x - total / 2.0, "r": x - total}[anchor[0]]
        baseline = y if anchor[1] == "s" else y + ink / 2.0
        scale = size / strokefont.EM
        stroke = max(0.9, size * 0.055 * weight)
        paint = rgb(fill)
        for char, advance in zip(string, advances):
            nominal, polylines = strokefont.glyph(char)
            left = pen + (advance - nominal * scale) / 2.0
            for polyline in polylines:
                pixels = [
                    (left + gx * scale, baseline - gy * scale) for gx, gy in polyline
                ]
                self.line(pixels, paint, stroke)
            pen += advance

    # -- rasteriser ------------------------------------------------------------------------

    def _fill(self, points, paint):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        top = max(0, int(math.floor(min(ys))))
        bottom = min(self.height, int(math.ceil(max(ys))))
        left = max(0, int(math.floor(min(xs))))
        right = min(self.width, int(math.ceil(max(xs))) + 1)
        if bottom <= top or right <= left:
            return
        span = right - left
        edges = [
            (ax, ay, bx, by)
            for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1])
            if ay != by
        ]
        if not edges:
            return
        weight = 1.0 / SUBSAMPLES
        run = paint * span
        for row in range(top, bottom):
            diff = [0.0] * (span + 2)
            partial = {}
            hit = False
            for sub in range(SUBSAMPLES):
                sy = row + (sub + 0.5) / SUBSAMPLES
                crossings = [
                    ax + (sy - ay) * (bx - ax) / (by - ay)
                    for ax, ay, bx, by in edges
                    if (ay <= sy < by) or (by <= sy < ay)
                ]
                if len(crossings) < 2:
                    continue
                crossings.sort()
                for i in range(0, len(crossings) - 1, 2):
                    if self._span(
                        crossings[i] - left,
                        crossings[i + 1] - left,
                        weight,
                        span,
                        diff,
                        partial,
                    ):
                        hit = True
            if not hit:
                continue
            self._blend_row(row, left, span, diff, partial, paint, run)

    @staticmethod
    def _span(x0, x1, weight, span, diff, partial):
        x0 = max(0.0, x0)
        x1 = min(float(span), x1)
        if x1 - x0 <= 1e-9:
            return False
        first = int(x0)
        last = int(math.ceil(x1))
        if last - first == 1:
            partial[first] = partial.get(first, 0.0) + (x1 - x0) * weight
            return True
        partial[first] = partial.get(first, 0.0) + (first + 1 - x0) * weight
        partial[last - 1] = partial.get(last - 1, 0.0) + (x1 - (last - 1)) * weight
        if last - 1 > first + 1:
            diff[first + 1] += weight
            diff[last - 1] -= weight
        return True

    def _blend_row(self, row, left, span, diff, partial, paint, run):
        buffer = self.buffer
        base = row * self.stride + left * 3
        acc = 0.0
        solid = -1
        column = 0
        pr, pg, pb = paint[0], paint[1], paint[2]
        while column < span:
            acc += diff[column]
            coverage = acc + partial.get(column, 0.0)
            if coverage >= 0.996:
                if solid < 0:
                    solid = column
            else:
                if solid >= 0:
                    buffer[base + solid * 3 : base + column * 3] = run[
                        : (column - solid) * 3
                    ]
                    solid = -1
                if coverage > 0.004:
                    offset = base + column * 3
                    inverse = 1.0 - coverage
                    buffer[offset] = int(buffer[offset] * inverse + pr * coverage + 0.5)
                    buffer[offset + 1] = int(
                        buffer[offset + 1] * inverse + pg * coverage + 0.5
                    )
                    buffer[offset + 2] = int(
                        buffer[offset + 2] * inverse + pb * coverage + 0.5
                    )
            column += 1
        if solid >= 0:
            buffer[base + solid * 3 : base + span * 3] = run[: (span - solid) * 3]

    # -- output ----------------------------------------------------------------------------

    def png_bytes(self, compression=6):
        raw = bytearray()
        for row in range(self.height):
            raw.append(0)  # filter type 0 (None)
            start = row * self.stride
            raw += self.buffer[start : start + self.stride]

        def chunk(tag, payload):
            body = tag + payload
            return (
                struct.pack(">I", len(payload))
                + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            )

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), compression))
            + chunk(b"IEND", b"")
        )

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.png_bytes())
        return path
