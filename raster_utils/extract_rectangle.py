# -*- coding: utf-8 -*-
"""
extract_rectangle.py
Выборка значений растра: по прямоугольнику / внутри полигона / вне полигона.

Ключевые свойства:
* окно выборки снапится к пиксельной сетке растра — возвращаются собственные
  пиксели растра, без неявного ресемплинга (см. block_io.align_rect_to_raster);
* блок читается одним куском через block.data() → numpy, без Python-цикла
  по пикселям;
* у всех каналов ОДНА общая маска валидности, поэтому результат — корректная
  матрица (n_pixels, n_bands): values[i, a] и values[i, b] — это один и тот же
  пиксель. Именно это нужно для спектральных сигнатур, ковариации, SAM
  и расстояния Махаланобиса.

Все координаты (rect / geom) ожидаются В CRS РАСТРА.
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

#: Пикселей на порцию чтения. Окно обрабатывается полосами, иначе координатная
#: сетка и блок в float64 выделяются на всё окно сразу: для 60 млн пикселей это
#: 1 ГБ только на сетку плюс 0.5 ГБ на канал — QGIS падает на первом же крупном
#: объекте.
CHUNK_PIXELS = 2_000_000

#: Сколько пикселей максимум попадает в результат. Средние, ковариация и
#: гистограммы по 300 тыс. пикселей неотличимы от расчёта по десяткам
#: миллионов, а память и время отличаются на порядки. Факт прореживания
#: записывается в RasterSample и уезжает в JSON — молча оно не происходит.
DEFAULT_MAX_VALUES = 300_000


# ---------------------------------------------------------------------------
# Результат выборки
# ---------------------------------------------------------------------------

class RasterSample:
    """
    Набор пикселей растра, отобранных по геометрии.

    Attributes
    ----------
    band_names : list[str]                 названия каналов (n_bands)
    values     : np.ndarray (n_pixels, n_bands), float64, без NaN
    rect       : QgsRectangle              фактическое окно чтения (снапнутое)
    shape      : (nrows, ncols)            размер окна в пикселях
    mask       : np.ndarray (nrows*ncols,) bool — какие пиксели окна попали
                                           в выборку (row-major)
    """

    __slots__ = ('band_names', 'values', 'rect', 'shape', 'mask', 'total_pixels')

    def __init__(self, band_names, values, rect, shape, mask, total_pixels=None):
        self.band_names = list(band_names)
        self.values = values
        self.rect = rect
        self.shape = shape
        self.mask = mask
        #: сколько валидных пикселей было ДО прореживания
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
        """{band_name: 1-D array}. Длины массивов одинаковы у всех каналов."""
        if not self:
            return {}
        return {name: self.values[:, i] for i, name in enumerate(self.band_names)}


# ---------------------------------------------------------------------------
# Ядро выборки
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
    Отобрать пиксели растра.

    Parameters
    ----------
    rect : QgsRectangle   окно (для mode='bbox'); игнорируется, если задан geom
    geom : QgsGeometry    полигон в CRS растра (обязателен для polygon/outside)
    mode : 'bbox'     — все пиксели окна
           'polygon'  — центры пикселей внутри полигона
           'outside'  — центры пикселей в bbox полигона, но снаружи него

    Returns
    -------
    RasterSample либо None (нет пересечения / нет валидных пикселей).

    Raises
    ------
    SampleWindowTooLarge — если окно слишком велико.
    """
    if raster_layer is None:
        return None
    provider = raster_layer.dataProvider()
    if provider is None:
        return None

    # --- геометрия и окно ---------------------------------------------------
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

    # --- чтение полосами ----------------------------------------------------
    # Окно может быть огромным, поэтому и маска, и данные считаются порциями:
    # пик памяти держится в десятках МБ независимо от размера объекта.
    rows_per_chunk = max(1, min(nrows, CHUNK_PIXELS // max(ncols, 1)))
    pixel_h = window.height() / nrows

    chunks, index_chunks = [], []
    total_valid = 0

    for r0 in range(0, nrows, rows_per_chunk):
        r1 = min(r0 + rows_per_chunk, nrows)
        progress(f"Чтение строк {r0}–{r1} из {nrows}…", r0 / nrows * 100)

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
                # канал не прочитался — пиксели уйдут по маске валидности
                arr = np.full((sub_rows, ncols), np.nan)
            else:
                mask_source_nodata(arr, provider, band_idx)
            band_columns.append(arr.ravel()[selected])
            del arr

        values_chunk = np.column_stack(band_columns)
        del band_columns

        # --- ОДНА маска валидности на все каналы ---
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

    # --- прореживание, если пикселей слишком много --------------------------
    if max_values and values.shape[0] > max_values:
        step = int(np.ceil(values.shape[0] / max_values))
        values = values[::step]
        indices = indices[::step]
        progress(f"Прорежено: {total_valid} → {values.shape[0]} пикс.", 95)

    used = np.zeros(ncols * nrows, dtype=bool)
    used[indices] = True

    progress("Готово", 100)
    return RasterSample(names, values, window, (nrows, ncols), used,
                        total_pixels=total_valid)


# ---------------------------------------------------------------------------
# Совместимые обёртки: {band_name: 1-D array}
# ---------------------------------------------------------------------------

def extract_raster_values_in_rect(raster_layer, rect, buffer_percent=0.0, progress=_NOOP):
    """Значения всех каналов в прямоугольнике (координаты — в CRS растра)."""
    sample = sample_raster(
        raster_layer, rect=rect, mode=MODE_BBOX,
        buffer_percent=buffer_percent, progress=progress,
    )
    return sample.as_dict() if sample else {}


def extract_raster_values_in_geometry(
    raster_layer, geom, buffer_percent=0.0, eps=1e-12, progress=_NOOP
):
    """Значения каналов для центров пикселей внутри полигона."""
    sample = sample_raster(
        raster_layer, geom=geom, mode=MODE_POLYGON,
        buffer_percent=buffer_percent, eps=eps, progress=progress,
    )
    return sample.as_dict() if sample else {}


def extract_raster_values_outside_geometry(raster_layer, geom, eps=1e-12, progress=_NOOP):
    """Значения каналов в bbox полигона, но вне самого полигона (фон)."""
    sample = sample_raster(
        raster_layer, geom=geom, mode=MODE_OUTSIDE,
        eps=eps, progress=progress,
    )
    return sample.as_dict() if sample else {}


def get_feature_bounding_rect(features, buffer_meters=0):
    """
    Общий bbox объектов В CRS ИХ СЛОЯ.
    ВНИМАНИЕ: если CRS слоя отличается от CRS растра, результат нельзя
    передавать в выборку напрямую — сначала перепроецируйте геометрию.
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
