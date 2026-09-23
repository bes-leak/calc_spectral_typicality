# -*- coding: utf-8 -*-
"""
raster_layer.py
Кеш обзорных гистограмм растрового слоя.

Хранятся ТОЛЬКО гистограммы (counts + edges), а не сами значения: раньше объект
держал полный массив растра (as_numpy()) и жил до выгрузки плагина. Теперь
объём памяти — O(число бинов), независимо от размера снимка.
"""

from ..raster_utils.histogram import histogram
from ..raster_utils.overview import DEFAULT_MAX_PIXELS, iter_overview_bands

_NOOP = lambda msg, pct=None: None


class RasterLayer(object):
    """Имя слоя + гистограммы его каналов (по децимированной выборке)."""

    def __init__(self, name: str, histograms: dict, n_pixels: int = 0):
        self.name = name
        self._histograms = histograms
        self.n_pixels = n_pixels
        self.n_bounds = len(histograms)

    @classmethod
    def from_layer(cls, qgis_layer, max_pixels: int = DEFAULT_MAX_PIXELS,
                   max_zeros: int = 20, step: int = 5, progress=_NOOP):
        """Построить гистограммы по всему растру, не загружая его целиком."""
        histograms = {}
        n_pixels = 0
        for band_name, values in iter_overview_bands(qgis_layer, max_pixels, progress):
            n_pixels = max(n_pixels, int(values.size))
            histograms[band_name] = histogram(values, max_zeros, step)
            del values

        print(f'Растровый слой {qgis_layer.name()}: {len(histograms)} канал(ов), '
              f'обзорная выборка {n_pixels} пикс./канал')
        return cls(qgis_layer.name(), histograms, n_pixels)

    def histogram(self) -> dict:
        """{band_name: (counts, edges)}"""
        return self._histograms

    def __bool__(self) -> bool:
        return bool(self._histograms)
