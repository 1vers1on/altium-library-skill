"""Convex-outline geometry for footprint clearance checks and previews.

Every primitive is reduced to a polygon in millimetres so one distance routine covers round,
oval, rectangular, octagonal and rounded-rectangular pads at any rotation, plus track and arc
strokes. Curves are approximated by segments, so a reported clearance is a slight under-estimate
of the true one — never an over-estimate.
"""

import math

SEGMENTS = 32  # per full circle


def rotate(point, center, degrees):
    if not degrees:
        return point
    a = math.radians(degrees)
    dx = point[0] - center[0]
    dy = point[1] - center[1]
    return (
        center[0] + dx * math.cos(a) - dy * math.sin(a),
        center[1] + dx * math.sin(a) + dy * math.cos(a),
    )


def rounded_rect(center, size, corner, rotation=0.0, segments=SEGMENTS):
    """Rectangle with `corner` mm corner radius; corner >= min(size)/2 gives a stadium or circle."""
    cx, cy = center
    w, h = size[0] / 2, size[1] / 2
    corner = max(0.0, min(corner, min(w, h)))
    points = []
    quadrants = (
        (w - corner, h - corner, 0),
        (-(w - corner), h - corner, 90),
        (-(w - corner), -(h - corner), 180),
        (w - corner, -(h - corner), 270),
    )
    steps = max(2, segments // 4)
    for ox, oy, start in quadrants:
        for i in range(steps + 1):
            a = math.radians(start + 90.0 * i / steps)
            points.append(
                (
                    cx + ox + corner * math.cos(a),
                    cy + oy + corner * math.sin(a),
                )
            )
    if corner == 0:
        points = [
            (cx + w, cy + h),
            (cx - w, cy + h),
            (cx - w, cy - h),
            (cx + w, cy - h),
        ]
    return [rotate(p, center, rotation) for p in points]


def octagon(center, size, rotation=0.0):
    cx, cy = center
    w, h = size[0] / 2, size[1] / 2
    c = min(w, h) / 2
    points = [
        (cx - w + c, cy - h),
        (cx + w - c, cy - h),
        (cx + w, cy - h + c),
        (cx + w, cy + h - c),
        (cx + w - c, cy + h),
        (cx - w + c, cy + h),
        (cx - w, cy + h - c),
        (cx - w, cy - h + c),
    ]
    return [rotate(p, center, rotation) for p in points]


def pad_outline(center, size, shape, rotation=0.0, corner_percent=50, grow=0.0):
    """Copper (or mask, with `grow`) outline of a pad. `shape` is Altium's code."""
    size = (size[0] + 2 * grow, size[1] + 2 * grow)
    if shape == 1:
        return rounded_rect(center, size, min(size) / 2, rotation)  # round / oval
    if shape == 2:
        return rounded_rect(center, size, 0.0, rotation)  # rectangular
    if shape == 3:
        return octagon(center, size, rotation)  # octagonal
    return rounded_rect(
        center, size, min(size) / 2 * corner_percent / 100.0, rotation
    )  # rounded rectangle


def slot_outline(center, width, length, rotation=0.0):
    return rounded_rect(
        center,
        (max(width, length), min(width, length)),
        min(width, length) / 2,
        rotation,
    )


def stroke_outline(start, end, width, segments=SEGMENTS):
    """A track drawn with round caps, as a polygon."""
    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length == 0:
        return rounded_rect(start, (width, width), width / 2)
    angle = math.degrees(math.atan2(dy, dx))
    center = ((x0 + x1) / 2, (y0 + y1) / 2)
    return rounded_rect(center, (length + width, width), width / 2, angle, segments)


def arc_points(center, radius, start_angle, end_angle, segments=SEGMENTS):
    sweep = (end_angle - start_angle) % 360 or 360.0
    steps = max(2, int(math.ceil(segments * sweep / 360.0)))
    return [
        (
            center[0]
            + radius * math.cos(math.radians(start_angle + sweep * i / steps)),
            center[1]
            + radius * math.sin(math.radians(start_angle + sweep * i / steps)),
        )
        for i in range(steps + 1)
    ]


def _segment_distance(a, b, c, d):
    def point_segment(p, q, r):
        qx, qy = q
        rx, ry = r
        dx, dy = rx - qx, ry - qy
        if dx == dy == 0:
            return math.hypot(p[0] - qx, p[1] - qy)
        t = max(
            0.0,
            min(
                1.0,
                ((p[0] - qx) * dx + (p[1] - qy) * dy) / (dx * dx + dy * dy),
            ),
        )
        return math.hypot(p[0] - qx - t * dx, p[1] - qy - t * dy)

    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d1, d2, d3, d4 = side(c, d, a), side(c, d, b), side(a, b, c), side(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(
        point_segment(a, c, d),
        point_segment(b, c, d),
        point_segment(c, a, b),
        point_segment(d, a, b),
    )


def contains(polygon, point):
    x, y = point
    inside = False
    for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def distance(a, b):
    """Closest approach between two polygons; 0.0 when they touch, overlap or nest."""
    if contains(a, b[0]) or contains(b, a[0]):
        return 0.0
    best = float("inf")
    for p, q in zip(a, a[1:] + a[:1]):
        for r, s in zip(b, b[1:] + b[:1]):
            best = min(best, _segment_distance(p, q, r, s))
            if best == 0.0:
                return 0.0
    return best


def bounds(polygons):
    xs = [p[0] for poly in polygons for p in poly]
    ys = [p[1] for poly in polygons for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))
