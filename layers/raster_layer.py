# -*- coding: utf-8 -*-
"""
raster_layer.py
Cache of overview histograms for a raster layer.

ONLY the histograms (counts + edges) are kept, never the values themselves:
this object used to hold the full raster array (as_numpy()) and lived until the
plugin was unloaded. Memory is now O(number of bins), whatever the scene size.
"""

from ..raster_utils.histogram import histogram
from ..raster_utils.overview import DEFAULT_MAX_PIXELS, iter_overview_bands

_NOOP = lambda msg, pct=None: None


class RasterLayer(object):
    """Layer name plus the histograms of its bands (from a decimated sample)."""

    def __init__(self, name: str, histograms: dict, n_pixels: int = 0):
        self.name = name
        self._histograms = histograms
        self.n_pixels = n_pixels
        self.n_bounds = len(histograms)

    @classmethod
    def from_layer(cls, qgis_layer, max_pixels: int = DEFAULT_MAX_PIXELS,
                   max_zeros: int = 20, step: int = 5, progress=_NOOP):
        """Build histograms over the whole raster without loading it entirely."""
        histograms = {}
        n_pixels = 0
        for band_name, values in iter_overview_bands(qgis_layer, max_pixels, progress):
            n_pixels = max(n_pixels, int(values.size))
            histograms[band_name] = histogram(values, max_zeros, step)
            del values

        print(f'Raster layer {qgis_layer.name()}: {len(histograms)} band(s), '
              f'overview sample {n_pixels} px/band')
        return cls(qgis_layer.name(), histograms, n_pixels)

    def histogram(self) -> dict:
        """{band_name: (counts, edges)}"""
        return self._histograms

    def __bool__(self) -> bool:
        return bool(self._histograms)
