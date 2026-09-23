# -*- coding: utf-8 -*-
"""
extract_rectangle.py
Sampling raster values: by rectangle / inside a polygon / outside a polygon.

Key properties:
* the sampling window is snapped to the raster pixel grid, so the raster's own
  pixels come back with no implicit resampling (see block_io.align_rect_to_raster);
* the block is read in one go through block.data() -> numpy, with no Python loop
  over pixels;
* all bands share ONE validity mask, so the result is a well-formed
  (n_pixels, n_bands) matrix: values[i, a] and values[i, b] are the same pixel.
  That is exactly what spectral signatures, covariance, SAM and the Mahalanobis
  distance require.

All coordinates (rect / geom) are expected IN THE RASTER CRS.
"""

import numpy as np
from qgis.core import QgsRectangle

from .block_io import (
    SampleWindowTooLarge,
    align_rect_to_raster,
    block_to_array,
    mask_source_nodata,
)
from .polygon_sampling import (
    geometry_to_rings_xy,
    point_in_rings_even_odd,        # kept for fallback / small areas
    points_in_rings_even_odd_vec,
    build_pixel_grid,
)

_NOOP = lambda msg, pct=None: None   # silent default callback

MODE_BBOX = 'bbox'
MODE_POLYGON = 'polygon'
MODE_OUTSIDE = 'outside'

#: Pixels per read chunk. The window is processed in stripes; otherwise the
#: coordinate grid and the float64 block are allocated for the whole window at
#: once: for 60M pixels that is 1 GB for the grid alone plus 0.5 GB per band, and
#: QGIS dies on the very first large feature.
CHUNK_PIXELS = 2_000_000

#: Upper bound on how many pixels reach the result. Means, covariance and
#: histograms over 300k pixels are indistinguishable from those over tens of
#: millions, while memory and time differ by orders of magnitude. Subsampling is
#: recorded in RasterSample and exported to JSON; it never happens silently.
DEFAULT_MAX_VALUES = 300_000


# ---------------------------------------------------------------------------
# Sampling result
# ---------------------------------------------------------------------------

class RasterSample:
    """
    The set of raster pixels selected by a geometry.

    Attributes
    ----------
    band_names : list[str]                 band names (n_bands)
    values     : np.ndarray (n_pixels, n_bands), float64, free of NaN
    rect       : QgsRectangle              the actual (snapped) read window
    shape      : (nrows, ncols)            window size in pixels
    mask       : np.ndarray (nrows*ncols,) bool - which window pixels made it
                                           into the sample (row-major)
    """

    __slots__ = ('band_names', 'values', 'rect', 'shape', 'mask', 'total_pixels')

    def __init__(self, band_names, values, rect, shape, mask, total_pixels=None):
        self.band_names = list(band_names)
        self.values = values
        self.rect = rect
        self.shape = shape
        self.mask = mask
        #: how many valid pixels there were BEFORE subsampling
        self.total_pixels = int(total_pixels if total_pixels is not None
                                else values.shape[0])

    @property
    def subsampled(self):
        return self.total_pixels > self.n_pixels

    @property
    def n_pixels(self) -> int:
        return int(self.values.shape[0]) if self.values.size else 0

    @property
    def n_bands(self) -> int:
        return len(self.band_names)

    def __bool__(self) -> bool:
        return self.n_pixels > 0 and self.n_bands > 0

    def __len__(self) -> int:
        return self.n_pixels

    def band(self, name) -> np.ndarray:
        return self.values[:, self.band_names.index(name)]

    def as_dict(self) -> dict:
        """{band_name: 1-D array}. Every band array has the same length."""
        if not self:
            return {}
        return {name: self.values[:, i] for i, name in enumerate(self.band_names)}


# ---------------------------------------------------------------------------
# Sampling core
# ---------------------------------------------------------------------------

def _band_name(raster_layer, band_idx: int) -> str:
    try:
        name = raster_layer.bandName(band_idx)
    except Exception:
        name = None
    return name or f"Band {band_idx}"


def sample_raster(
    raster_layer,
    rect=None,
    geom=None,
    mode: str = MODE_BBOX,
    buffer_percent: float = 0.0,
    eps: float = 1e-12,
    max_values: int = DEFAULT_MAX_VALUES,
    progress=_NOOP,
):
    """
    Select raster pixels.

    Parameters
    ----------
    rect : QgsRectangle   the window (for mode='bbox'); ignored when geom is given
    geom : QgsGeometry    polygon in the raster CRS (required for polygon/outside)
    mode : 'bbox'     - every pixel of the window
           'polygon'  - pixel centres inside the polygon
           'outside'  - pixel centres inside the polygon bbox but outside the
                        polygon itself

    Returns
    -------
    RasterSample, or None (no intersection / no valid pixels).

    Raises
    ------
    SampleWindowTooLarge - if the window is too large.
    """
    if raster_layer is None:
        return None
    provider = raster_layer.dataProvider()
    if provider is None:
        return None

    # --- geometry and window ------------------------------------------------
    rings = []
    if mode in (MODE_POLYGON, MODE_OUTSIDE):
        if geom is None or geom.isEmpty():
            return None
        rings = geometry_to_rings_xy(geom)
        if not rings:
            return None

    base_rect = geom.boundingBox() if (geom is not None and not geom.isEmpty()) else rect
    if base_rect is None:
        return None

    aligned = align_rect_to_raster(raster_layer, base_rect, buffer_percent)
    if aligned is None:
        return None
    window, ncols, nrows = aligned

    n_bands = provider.bandCount()
    if n_bands < 1:
        return None
    names = [_band_name(raster_layer, i + 1) for i in range(n_bands)]

    # --- reading in stripes -------------------------------------------------
    # The window may be huge, so both mask and data are computed chunk by chunk:
    # peak memory stays in the tens of MB whatever the size of the feature.
    rows_per_chunk = max(1, min(nrows, CHUNK_PIXELS // max(ncols, 1)))
    pixel_h = window.height() / nrows

    chunks, index_chunks = [], []
    total_valid = 0

    for r0 in range(0, nrows, rows_per_chunk):
        r1 = min(r0 + rows_per_chunk, nrows)
        progress(f"Reading rows {r0}–{r1} of {nrows}…", r0 / nrows * 100)

        sub_rect = QgsRectangle(
            window.xMinimum(), window.yMaximum() - r1 * pixel_h,
            window.xMaximum(), window.yMaximum() - r0 * pixel_h,
        )
        sub_rows = r1 - r0

        if mode == MODE_BBOX:
            selected = np.arange(sub_rows * ncols)
        else:
            xs, ys = build_pixel_grid(sub_rect, ncols, sub_rows)
            inside = points_in_rings_even_odd_vec(xs, ys, rings, eps=eps)
            del xs, ys
            spatial = inside if mode == MODE_POLYGON else ~inside
            selected = np.flatnonzero(spatial)
            del spatial, inside
        if selected.size == 0:
            continue

        band_columns = []
        for band_idx in range(1, n_bands + 1):
            block = provider.block(band_idx, sub_rect, ncols, sub_rows)
            arr = block_to_array(block)
            if arr is None:
                # the band could not be read; its pixels drop out via the validity mask
                arr = np.full((sub_rows, ncols), np.nan)
            else:
                mask_source_nodata(arr, provider, band_idx)
            band_columns.append(arr.ravel()[selected])
            del arr

        values_chunk = np.column_stack(band_columns)
        del band_columns

        # --- ONE validity mask shared by all bands ---
        valid = np.all(np.isfinite(values_chunk), axis=1)
        if not valid.any():
            continue
        chunks.append(values_chunk[valid])
        index_chunks.append(r0 * ncols + selected[valid])
        total_valid += int(valid.sum())
        del values_chunk, valid, selected

    if not chunks:
        return None

    values = np.concatenate(chunks)
    indices = np.concatenate(index_chunks)
    del chunks, index_chunks

    # --- subsampling when there are too many pixels -------------------------
    if max_values and values.shape[0] > max_values:
        step = int(np.ceil(values.shape[0] / max_values))
        values = values[::step]
        indices = indices[::step]
        progress(f"Subsampled: {total_valid} → {values.shape[0]} px", 95)

    used = np.zeros(ncols * nrows, dtype=bool)
    used[indices] = True

    progress("Done", 100)
    return RasterSample(names, values, window, (nrows, ncols), used,
                        total_pixels=total_valid)


# ---------------------------------------------------------------------------
# Compatibility wrappers: {band_name: 1-D array}
# ---------------------------------------------------------------------------

def extract_raster_values_in_rect(raster_layer, rect, buffer_percent=0.0, progress=_NOOP):
    """Values of every band within a rectangle (coordinates in the raster CRS)."""
    sample = sample_raster(
        raster_layer, rect=rect, mode=MODE_BBOX,
        buffer_percent=buffer_percent, progress=progress,
    )
    return sample.as_dict() if sample else {}


def extract_raster_values_in_geometry(
    raster_layer, geom, buffer_percent=0.0, eps=1e-12, progress=_NOOP
):
    """Band values for pixel centres inside a polygon."""
    sample = sample_raster(
        raster_layer, geom=geom, mode=MODE_POLYGON,
        buffer_percent=buffer_percent, eps=eps, progress=progress,
    )
    return sample.as_dict() if sample else {}


def extract_raster_values_outside_geometry(raster_layer, geom, eps=1e-12, progress=_NOOP):
    """Band values inside the polygon bbox but outside the polygon itself (background)."""
    sample = sample_raster(
        raster_layer, geom=geom, mode=MODE_OUTSIDE,
        eps=eps, progress=progress,
    )
    return sample.as_dict() if sample else {}


def get_feature_bounding_rect(features, buffer_meters=0):
    """
    The combined bbox of the features IN THEIR OWN LAYER CRS.
    NOTE: if the layer CRS differs from the raster CRS, the result must not be
    passed to the sampler directly - reproject the geometry first.
    """
    minX, minY = float('inf'), float('inf')
    maxX, maxY = float('-inf'), float('-inf')
    for feature in features:
        geom = feature.geometry()
        if not geom or geom.isEmpty():
            continue
        r = geom.boundingBox()
        minX = min(r.xMinimum(), minX)
        minY = min(r.yMinimum(), minY)
        maxX = max(r.xMaximum(), maxX)
        maxY = max(r.yMaximum(), maxY)
    if minX == float('inf'):
        return None
    rect = QgsRectangle(minX, minY, maxX, maxY)
    if buffer_meters > 0:
        rect = rect.buffered(buffer_meters)
    return rect
