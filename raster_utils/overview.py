# -*- coding: utf-8 -*-
"""
overview.py
Overview sampling across the WHOLE raster, for the band distribution histogram.

This used to call QgsRasterLayer.as_numpy(), which reads the raster in full:
for a Sentinel/Landsat scene that means gigabytes of RAM, and permanently so,
since the result was cached. A decimated sample is read instead: the provider is
asked for a reduced-size block, and GDAL picks an overview level if one exists.

The sample is APPROXIMATE - it describes the shape of the layer-wide
distribution, not an exact per-pixel count. For per-pixel analysis of features
use sample_raster() from extract_rectangle.py.
"""

import math

import numpy as np

from .block_io import block_to_array, mask_source_nodata

_NOOP = lambda msg, pct=None: None

# ~2M pixels per band: the histogram is already indistinguishable from the full
# one, while memory and time stay within reason.
DEFAULT_MAX_PIXELS = 2_000_000


def decimated_grid_size(x_size: int, y_size: int, max_pixels: int = DEFAULT_MAX_PIXELS):
    """Overview grid size (ncols, nrows), preserving the aspect ratio."""
    if x_size <= 0 or y_size <= 0:
        return None
    total = x_size * y_size
    if total <= max_pixels:
        return x_size, y_size
    factor = math.sqrt(total / float(max_pixels))
    return max(1, int(x_size / factor)), max(1, int(y_size / factor))


def iter_overview_bands(raster_layer, max_pixels: int = DEFAULT_MAX_PIXELS, progress=_NOOP):
    """
    Generator of (band_name, values) over every band of the raster.

    Each band array is yielded and released one at a time, so only a single band
    is resident at any moment. NoData values are dropped.
    """
    if raster_layer is None:
        return
    provider = raster_layer.dataProvider()
    if provider is None:
        return

    extent = raster_layer.extent()
    size = decimated_grid_size(provider.xSize(), provider.ySize(), max_pixels)
    if size is None or extent.width() <= 0 or extent.height() <= 0:
        return
    ncols, nrows = size

    n_bands = provider.bandCount()
    for i in range(n_bands):
        band_idx = i + 1
        try:
            name = raster_layer.bandName(band_idx) or f"Band {band_idx}"
        except Exception:
            name = f"Band {band_idx}"

        progress(f"Reading {name}…", (i / max(n_bands, 1)) * 100)

        block = provider.block(band_idx, extent, ncols, nrows)
        arr = block_to_array(block)
        if arr is None:
            continue
        mask_source_nodata(arr, provider, band_idx)

        values = arr.ravel()
        values = values[np.isfinite(values)]
        del arr
        if values.size:
            yield name, values

    progress("Done", 100)
