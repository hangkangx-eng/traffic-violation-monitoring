from __future__ import annotations

import math


def point_in_polygon(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        intersect = ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / ((y2 - y1) + 1e-6) + x1
        )
        if intersect:
            inside = not inside
    return inside


def _line_side(point: tuple[float, float], line: list[list[float]]) -> float:
    (x1, y1), (x2, y2) = line
    px, py = point
    return (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)


def crosses_line(
    prev_point: tuple[float, float], curr_point: tuple[float, float], line: list[list[float]]
) -> bool:
    if len(line) != 2:
        return False
    prev_side = _line_side(prev_point, line)
    curr_side = _line_side(curr_point, line)
    return (prev_side > 0 and curr_side < 0) or (prev_side < 0 and curr_side > 0)


def vector_norm(vec: tuple[float, float]) -> float:
    return math.sqrt(vec[0] * vec[0] + vec[1] * vec[1])


def cosine_similarity(a: tuple[float, float], b: tuple[float, float]) -> float:
    na = vector_norm(a)
    nb = vector_norm(b)
    if na < 1e-6 or nb < 1e-6:
        return 1.0
    return (a[0] * b[0] + a[1] * b[1]) / (na * nb)
