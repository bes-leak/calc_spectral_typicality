# -*- coding: utf-8 -*-
"""
overview.py
Обзорная выборка по ВСЕМУ растру — для гистограммы распределения каналов слоя.

Раньше здесь использовался QgsRasterLayer.as_numpy(): он читает растр целиком,
то есть для сцены Sentinel/Landsat это гигабайты в оперативной памяти, да ещё и
навсегда (результат кешировался). Здесь вместо этого читается децимированная
выборка: провайдеру запрашивается блок уменьшенного размера, и GDAL сам берёт
обзорный уровень (overview), если он есть.

Выборка ПРИБЛИЖЁННАЯ — это статистика формы распределения по слою, а не точный
подсчёт по каждому пикселю. Для попиксельного анализа объектов используйте
sample_raster() из extract_rectangle.py.
"""

import math

import numpy as np

from .block_io import block_to_array, mask_source_nodata

_NOOP = lambda msg, pct=None: None

# ~2 млн пикселей на канал: гистограмма от этого уже неотличима от полной,
# а память и время остаются в разумных пределах.
DEFAULT_MAX_PIXELS = 2_000_000


def decimated_grid_size(x_size: int, y_size: int, max_pixels: int = DEFAULT_MAX_PIXELS):
    """Размер обзорной сетки (ncols, nrows) с сохранением пропорций."""
    if x_size <= 0 or y_size <= 0:
        return None
    total = x_size * y_size
    if total <= max_pixels:
        return x_size, y_size
    factor = math.sqrt(total / float(max_pixels))
    return max(1, int(x_size / factor)), max(1, int(y_size / factor))


def iter_overview_bands(raster_layer, max_pixels: int = DEFAULT_MAX_PIXELS, progress=_NOOP):
    """
    Генератор (band_name, values) по всем каналам растра.

    Массив каждого канала отдаётся и освобождается по одному — в памяти
    единовременно живёт только один канал. Значения NoData отброшены.
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

        progress(f"Чтение {name}…", (i / max(n_bands, 1)) * 100)

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

    progress("Готово", 100)
