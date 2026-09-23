from typing import List, Sequence, Tuple

import numpy as np
from qgis.core import QgsGeometry, QgsWkbTypes

PointXY = Tuple[float, float]
Ring = Sequence[PointXY]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _point_on_segment(px, py, ax, ay, bx, by, eps):
    if px < min(ax, bx) - eps or px > max(ax, bx) + eps or \
       py < min(ay, by) - eps or py > max(ay, by) + eps:
        return False
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > eps:
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    if dot < -eps:
        return False
    seg_len2 = (bx - ax) ** 2 + (by - ay) ** 2
    return dot <= seg_len2 + eps


def geometry_to_rings_xy(geom: QgsGeometry) -> List[List[PointXY]]:
    if geom is None or geom.isEmpty():
        return []
    # asPolygon()/asMultiPolygon() raise ValueError on points and lines
    if geom.type() != QgsWkbTypes.PolygonGeometry:
        return []
    rings: List[List[PointXY]] = []
    if geom.isMultipart():
        for poly in geom.asMultiPolygon():
            for ring in poly:
                rings.append([(p.x(), p.y()) for p in ring])
    else:
        for ring in geom.asPolygon():
            rings.append([(p.x(), p.y()) for p in ring])
    return [r for r in rings if len(r) >= 3]


# ---------------------------------------------------------------------------
# Scalar PIP (original — kept for compatibility)
# ---------------------------------------------------------------------------

def point_in_rings_even_odd(px, py, rings, eps=1e-12):
    rings_list = list(rings)
    if not rings_list:
        return False
    for ring in rings_list:
        for (vx, vy) in ring:
            if abs(px - vx) <= eps and abs(py - vy) <= eps:
                py = py + (10.0 * eps if eps > 0 else 1e-12)
                break
    inside = False
    for ring in rings_list:
        n = len(ring)
        if n < 3:
            continue
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if _point_on_segment(px, py, x1, y1, x2, y2, eps):
                return True
            if (y1 > py) != (y2 > py):
                x_int = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
                if x_int > px + eps:
                    inside = not inside
    return inside


# ---------------------------------------------------------------------------
# Vectorized PIP — main speedup
# ---------------------------------------------------------------------------

def points_in_rings_even_odd_vec(
    xs: np.ndarray,
    ys: np.ndarray,
    rings: List[List[PointXY]],
    eps: float = 1e-12,
) -> np.ndarray:
    """
    Even-odd ray-casting for N pixel centres simultaneously.

    Parameters
    ----------
    xs, ys : 1-D float64 arrays of pixel centre coordinates (length N)
    rings  : output of geometry_to_rings_xy (outer ring + holes)

    Returns
    -------
    inside : bool array of shape (N,)

    Performance
    -----------
    Replaces a Python loop over every pixel with numpy broadcasting.
    For a 1000×1000 bbox and a 100-vertex polygon the speedup is ~100-500×.
    """
    inside = np.zeros(len(xs), dtype=bool)

    for ring in rings:
        if len(ring) < 3:
            continue
        arr = np.asarray(ring, dtype=np.float64)
        rx, ry = arr[:, 0], arr[:, 1]
        n = len(rx)

        for i in range(n):
            x1, y1 = rx[i],          ry[i]
            x2, y2 = rx[(i + 1) % n], ry[(i + 1) % n]

            # Half-open Y rule: edge crosses the ray if exactly one endpoint
            # is strictly above ys
            crosses = (y1 > ys) != (y2 > ys)
            if not np.any(crosses):
                continue

            dy = y2 - y1
            # Guard against horizontal edges (already excluded by crosses, but be safe)
            safe_dy = np.where(np.abs(dy) < eps, eps, dy)
            x_int = x1 + (ys - y1) * (x2 - x1) / safe_dy

            inside ^= crosses & (x_int > xs + eps)

    return inside


def build_pixel_grid(rect, block_width: int, block_height: int):
    """
    Return flat xs, ys arrays for pixel centre coordinates within `rect`
    (QgsRectangle), sized (block_height × block_width).
    Row-major order: pixel (row=y, col=x) → index y*block_width + x.
    """
    dx = rect.width()  / block_width
    dy = rect.height() / block_height

    col_idx = np.arange(block_width,  dtype=np.float64)
    row_idx = np.arange(block_height, dtype=np.float64)

    px = rect.xMinimum() + (col_idx + 0.5) * dx   # shape (W,)
    py = rect.yMaximum() - (row_idx + 0.5) * dy   # shape (H,)  y goes down

    # Full grid: broadcast to (H, W) then flatten
    xs = np.broadcast_to(px[np.newaxis, :], (block_height, block_width)).ravel()
    ys = np.broadcast_to(py[:, np.newaxis], (block_height, block_width)).ravel()
    return xs, ys