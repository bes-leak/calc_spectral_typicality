# -*- coding: utf-8 -*-
"""
block_io.py
Fast QgsRasterBlock -> numpy conversion, plus snapping the sampling window to
the pixel grid of the source raster.

Why:
* `block.value(y, x)` in a loop is one C++ call per pixel. For a 1000x1000
  window that is a million calls per band. `block.data()` hands over the whole
  buffer at once and numpy does the rest.
* `provider.block(band, rect, w, h)` resamples the data onto an arbitrary rect.
  If the rect is not aligned to the raster grid, what comes back is NOT the
  original pixels but a resampling of them, shifted by up to half a pixel.
  `align_rect_to_raster` snaps the window to the grid, so the read is exact.
"""

import math

import numpy as np
from qgis.core import Qgis, QgsRectangle

# Guard against an obviously meaningless window. Reading itself goes in stripes
# (extract_rectangle.CHUNK_PIXELS), so memory barely depends on the window size;
# this limit guards against a typo in the coordinates and against features the
# size of a whole scene, which there is no point in sampling in full anyway.
MAX_SAMPLE_PIXELS = 200_000_000


# ---------------------------------------------------------------------------
# Qgis.DataType → numpy dtype
# ---------------------------------------------------------------------------

# The enum names are stable, but the way to reach them has changed
# (Qgis.Byte in older versions, Qgis.DataType.Byte in newer ones).
_DTYPE_BY_NAME = {
    'Byte': np.uint8,
    'Int8': np.int8,
    'UInt16': np.uint16,
    'Int16': np.int16,
    'UInt32': np.uint32,
    'Int32': np.int32,
    'Float32': np.float32,
    'Float64': np.float64,
}

# The numeric enum values have not changed since QGIS 3.0, so use them as a fallback.
_DTYPE_BY_VALUE = {
    1: np.uint8,    # Byte
    2: np.uint16,   # UInt16
    3: np.int16,    # Int16
    4: np.uint32,   # UInt32
    5: np.int32,    # Int32
    6: np.float32,  # Float32
    7: np.float64,  # Float64
    14: np.int8,    # Int8 (QGIS >= 3.42)
}


def numpy_dtype_for(data_type):
    """Return the numpy dtype for a Qgis.DataType, or None if it is not numeric."""
    holder = getattr(Qgis, 'DataType', None) or Qgis
    try:
        raw = int(data_type)
    except (TypeError, ValueError):
        return None

    for name, np_type in _DTYPE_BY_NAME.items():
        member = getattr(holder, name, None)
        if member is None:
            continue
        try:
            if int(member) == raw:
                return np_type
        except (TypeError, ValueError):
            continue

    return _DTYPE_BY_VALUE.get(raw)


# ---------------------------------------------------------------------------
# Reading a block
# ---------------------------------------------------------------------------

def _mask_value(arr: np.ndarray, value: float, integral: bool) -> None:
    """In place: replace pixels equal to `value` with NaN."""
    if value is None:
        return
    value = float(value)
    if not np.isfinite(value):
        return
    if integral:
        hit = arr == value
    else:
        # rtol=0: we must not catch neighbouring legitimate values
        hit = np.isclose(arr, value, rtol=0.0, atol=abs(value) * 1e-9 + 1e-12)
    if hit.any():
        arr[hit] = np.nan


def block_to_array(block):
    """
    QgsRasterBlock -> np.ndarray[float64] of shape (height, width).
    NoData and non-numeric values are replaced with NaN.
    Returns None if the block is empty or its data type is unsupported.
    """
    if block is None:
        return None
    try:
        if not block.isValid():
            return None
    except AttributeError:
        pass

    width, height = block.width(), block.height()
    if width <= 0 or height <= 0:
        return None

    dtype = numpy_dtype_for(block.dataType())
    if dtype is None:
        return None

    raw = block.data()
    if raw is None:
        return None
    buf = bytes(raw)
    n_pixels = width * height
    itemsize = np.dtype(dtype).itemsize
    if len(buf) < n_pixels * itemsize:
        return None

    arr = np.frombuffer(buf, dtype=dtype, count=n_pixels).reshape(height, width)
    integral = np.issubdtype(dtype, np.integer)
    arr = arr.astype(np.float64)   # a copy: the source buffer is read-only

    try:
        if block.hasNoDataValue():
            _mask_value(arr, block.noDataValue(), integral)
    except AttributeError:
        pass

    arr[~np.isfinite(arr)] = np.nan
    return arr


def mask_source_nodata(arr: np.ndarray, provider, band_idx: int) -> None:
    """
    In place: mask out the provider's NoData values (source + user-defined).
    The block usually flags them already, but not every provider does.
    """
    integral = False
    try:
        integral = np.issubdtype(numpy_dtype_for(provider.dataType(band_idx)), np.integer)
    except Exception:
        pass

    try:
        if provider.sourceHasNoDataValue(band_idx):
            _mask_value(arr, provider.sourceNoDataValue(band_idx), integral)
    except Exception:
        pass

    try:
        for rng in provider.userNoDataValues(band_idx):
            lo, hi = float(rng.min()), float(rng.max())
            if np.isfinite(lo) and np.isfinite(hi):
                arr[(arr >= lo) & (arr <= hi)] = np.nan
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Snapping the window to the raster grid
# ---------------------------------------------------------------------------

class SampleWindowTooLarge(ValueError):
    """The sampling window does not fit in memory."""


def align_rect_to_raster(raster_layer, rect, buffer_percent: float = 0.0):
    """
    Snap `rect` (in the raster CRS) to the raster pixel grid and clip it to the
    raster extent.

    Returns
    -------
    (QgsRectangle, ncols, nrows), or None if there is no intersection.

    Raises
    ------
    SampleWindowTooLarge - if the window exceeds MAX_SAMPLE_PIXELS.
    """
    if rect is None or raster_layer is None:
        return None

    provider = raster_layer.dataProvider()
    if provider is None:
        return None

    extent = raster_layer.extent()
    x_size, y_size = provider.xSize(), provider.ySize()
    if x_size <= 0 or y_size <= 0 or extent.width() <= 0 or extent.height() <= 0:
        return None

    x_min, x_max = rect.xMinimum(), rect.xMaximum()
    y_min, y_max = rect.yMinimum(), rect.yMaximum()
    if x_max < x_min or y_max < y_min:
        return None

    if buffer_percent > 0:
        dx = (x_max - x_min) * buffer_percent
        dy = (y_max - y_min) * buffer_percent
        x_min, x_max = x_min - dx, x_max + dx
        y_min, y_max = y_min - dy, y_max + dy

    pixel_w = extent.width() / x_size
    pixel_h = extent.height() / y_size
    if pixel_w <= 0 or pixel_h <= 0:
        return None

    tol = 1e-9   # so floating-point noise does not add a spurious row/column
    col_min = int(math.floor((x_min - extent.xMinimum()) / pixel_w + tol))
    col_max = int(math.ceil((x_max - extent.xMinimum()) / pixel_w - tol))
    row_min = int(math.floor((extent.yMaximum() - y_max) / pixel_h + tol))
    row_max = int(math.ceil((extent.yMaximum() - y_min) / pixel_h - tol))

    col_min = min(max(col_min, 0), x_size)
    col_max = min(max(col_max, 0), x_size)
    row_min = min(max(row_min, 0), y_size)
    row_max = min(max(row_max, 0), y_size)

    ncols, nrows = col_max - col_min, row_max - row_min
    if ncols <= 0 or nrows <= 0:
        return None

    if ncols * nrows > MAX_SAMPLE_PIXELS:
        raise SampleWindowTooLarge(
            f"The analysis area is too large: {ncols}×{nrows} = "
            f"{ncols * nrows:,} pixels (limit {MAX_SAMPLE_PIXELS:,}).\n"
            "Pick a smaller feature, or a raster with a coarser resolution."
        )

    snapped = QgsRectangle(
        extent.xMinimum() + col_min * pixel_w,
        extent.yMaximum() - row_max * pixel_h,
        extent.xMinimum() + col_max * pixel_w,
        extent.yMaximum() - row_min * pixel_h,
    )
    return snapped, ncols, nrows
