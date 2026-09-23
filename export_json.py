# -*- coding: utf-8 -*-
"""
export_json.py
Экспорт спектральных характеристик объектов в JSON (формат v2).

Отличия от v1: запись на КАЖДЫЙ объект вместо объединённой геометрии. Оценка
типичности относится к объекту, поэтому объединение здесь не годится. Заодно в
запись кладутся атрибуты объекта — это и есть «копия shp в JSON», к которой мы
идём как к основному формату хранения.

Что в записи объекта:
  * атрибуты, центроид, контур (в CRS растра);
  * поканальная статистика внутри объекта и в фоне (охват минус объект);
  * вектор средних и ковариация — по ним метрики типичности можно пересчитать
    вне QGIS;
  * оценка типичности из хранилища (или "не указано").
"""

import json
from datetime import datetime

import numpy as np
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsGeometry

from .raster_utils.polygon_sampling import geometry_to_rings_xy
from .raster_utils.extract_rectangle import (
    MODE_BBOX,
    MODE_OUTSIDE,
    MODE_POLYGON,
    sample_raster,
)
from .typicality import label_for
from .typicality.profile import SpectralStats

FORMAT = "calc_sp_ch/feature-spectra"
VERSION = 2

_NOOP = lambda msg, pct=None: None


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Приведение значений к JSON
# ---------------------------------------------------------------------------

def _json_safe(value):
    """Атрибут QGIS → значение, которое переживёт json.dump."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (bytes, bytearray)):
        return f"<binary {len(value)} bytes>"
    if isinstance(value, np.generic):
        return _json_safe(value.item())

    is_null = getattr(value, "isNull", None)      # QVariant / QDate / QDateTime
    if callable(is_null):
        try:
            if is_null():
                return None
        except Exception:
            pass
    to_string = getattr(value, "toString", None)
    if callable(to_string):
        try:
            return to_string(Qt.ISODate) or None
        except TypeError:
            return to_string() or None
    return str(value)


def _clean(obj):
    """Рекурсивно убрать NaN/Inf — иначе получится невалидный JSON."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, np.generic):
        return _clean(obj.item())
    if isinstance(obj, np.ndarray):
        return _clean(obj.tolist())
    return obj


def _band_stats(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"count": 0}
    return {
        "count":  int(values.size),
        "mean":   float(np.mean(values)),
        "std":    float(np.std(values)),
        "min":    float(np.min(values)),
        "max":    float(np.max(values)),
        "p25":    float(np.percentile(values, 25)),
        "median": float(np.median(values)),
        "p75":    float(np.percentile(values, 75)),
    }


def _stats_by_band(sample) -> dict:
    if not sample:
        return {}
    return {name: _band_stats(sample.values[:, i])
            for i, name in enumerate(sample.band_names)}


def _boundary(geom: QgsGeometry) -> dict:
    rings = geometry_to_rings_xy(geom)
    return {
        "type": "MultiPolygon" if geom.isMultipart() else "Polygon",
        "rings": [[[float(p[0]), float(p[1])] for p in ring] for ring in rings],
    }


def _attributes(feature) -> dict:
    try:
        fields = feature.fields()
        return {fields.at(i).name(): _json_safe(feature.attribute(i))
                for i in range(fields.count())}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Запись об объекте
# ---------------------------------------------------------------------------

def build_feature_record(feature, geom_in_raster_crs, raster_layer, key=None,
                         typicality=None, include_outside=True, progress=_NOOP):
    """
    Одна запись JSON по объекту. Геометрия — уже в CRS растра.
    Объект без пикселей не выбрасывается: попадает в файл с n_pixels = 0 и
    пометкой, чтобы выгрузка оставалась полной копией слоя.
    """
    geom = geom_in_raster_crs
    notes = []

    record = {
        "id": int(feature.id()),
        "key": str(key) if key is not None else str(feature.id()),
        "attributes": _attributes(feature),
    }

    centroid = geom.centroid()
    if centroid is not None and not centroid.isEmpty():
        point = centroid.asPoint()
        record["centroid"] = {"x": float(point.x()), "y": float(point.y())}
    else:
        record["centroid"] = None
        notes.append("не удалось вычислить центроид")

    record["boundary"] = _boundary(geom)
    # та же геометрия одной строкой: читается shapely/geopandas без сборки колец
    try:
        record["geometry_wkt"] = geom.asWkt()
    except Exception:
        record["geometry_wkt"] = None
    record["bbox"] = {
        "xmin": geom.boundingBox().xMinimum(), "ymin": geom.boundingBox().yMinimum(),
        "xmax": geom.boundingBox().xMaximum(), "ymax": geom.boundingBox().yMaximum(),
    }

    inside = sample_raster(raster_layer, geom=geom, mode=MODE_POLYGON, progress=_NOOP)
    if not inside:
        inside = sample_raster(raster_layer, rect=geom.boundingBox(), mode=MODE_BBOX,
                               progress=_NOOP)
        if inside:
            notes.append("центры пикселей не попали внутрь контура — взят охват объекта")

    if inside:
        stats = SpectralStats.from_sample(inside)
        record["bands"] = list(inside.band_names)
        record["n_pixels"] = stats.n
        record["spectral_inside"] = _stats_by_band(inside)
        record["mean_vector"] = [float(v) for v in stats.mean]
        record["cov"] = [[float(v) for v in row] for row in stats.cov]
        record["cov_shrinkage"] = float(stats.shrinkage)
    else:
        record["bands"] = []
        record["n_pixels"] = 0
        record["spectral_inside"] = {}
        record["mean_vector"] = None
        record["cov"] = None
        notes.append("нет данных растра в пределах объекта")

    if include_outside:
        outside = sample_raster(raster_layer, geom=geom, mode=MODE_OUTSIDE, progress=_NOOP)
        record["spectral_outside"] = _stats_by_band(outside)
        if not outside:
            notes.append("фон пуст: контур занимает весь свой охват")

    record["typicality"] = _typicality_block(typicality)
    if notes:
        record["notes"] = notes
    return record


def _typicality_block(record):
    if not record:
        return {"value": None, "label": label_for(None), "source": None}
    block = {
        "value": record.get("value"),
        "label": label_for(record.get("value")),
        "source": record.get("source"),
    }
    if record.get("set_at"):
        block["set_at"] = record["set_at"]
    if record.get("auto"):
        block["auto"] = record["auto"]
    return block


# ---------------------------------------------------------------------------
# Экспорт
# ---------------------------------------------------------------------------

def build_export(vector_layer, raster_layer, feature_geoms, keys=None,
                 typicality=None, class_profile=None, class_profiles=None,
                 id_field=None, class_field=None,
                 selection="selected", include_outside=True, progress=_NOOP):
    """
    Собрать документ JSON v2.

    feature_geoms: [(feature, geometry в CRS растра)]
    keys:          [ключ объекта] той же длины (или None → fid)
    typicality:    {ключ: запись из TypicalityStore}
    """
    typicality = typicality or {}
    total = max(len(feature_geoms), 1)
    features = []

    for index, (feature, geom) in enumerate(feature_geoms):
        key = keys[index] if keys else str(feature.id())
        progress(f"Объект {index + 1} из {len(feature_geoms)}", index / total * 100)
        features.append(build_feature_record(
            feature, geom, raster_layer,
            key=key,
            typicality=typicality.get(str(key)),
            include_outside=include_outside,
        ))

    bands = []
    for record in features:
        if record.get("bands"):
            bands = record["bands"]
            break

    document = {
        "format": FORMAT,
        "version": VERSION,
        "created_at": _now(),
        "source": {
            "vector_layer": vector_layer.name(),
            "vector_crs": vector_layer.crs().authid(),
            "raster_layer": raster_layer.name(),
            "raster_crs": raster_layer.crs().authid(),
            "bands": bands,
            "id_field": id_field,
            "class_field": class_field,
            "selection": selection,
            "feature_count": len(features),
            "sampling": {"inside": "polygon",
                         "outside": "bbox_minus_polygon" if include_outside else None},
        },
        "typicality_scale": {str(k): v for k, v in
                             ((1, label_for(1)), (2, label_for(2)), (3, label_for(3)))},
        # class_profile — активный эталон (совместимость), class_profiles — вся
        # библиотека: с несколькими классами нужен профиль каждого
        "class_profile": class_profile.to_dict() if class_profile is not None else None,
        "class_profiles": ({name: p.to_dict() for name, p in class_profiles.items()}
                           if class_profiles else None),
        "features": features,
    }
    progress("Сборка JSON…", 99)
    return document


def export_features_json(filepath, **kwargs):
    """build_export(...) + запись в файл. Возвращает (путь, документ)."""
    progress = kwargs.get("progress", _NOOP)
    document = build_export(**kwargs)
    with open(filepath, "w", encoding="utf-8") as f:
        # allow_nan=False: если где-то остался NaN, лучше упасть здесь,
        # чем отдать наружу невалидный JSON
        json.dump(_clean(document), f, ensure_ascii=False, indent=2, allow_nan=False)
    progress("Сохранено ✅", 100)
    return filepath, document


# ---------------------------------------------------------------------------
# Обратная загрузка оценок
# ---------------------------------------------------------------------------

def load_typicality(filepath):
    """
    Прочитать оценки из ранее выгруженного JSON.

    Returns {"vector_layer", "id_field", "records": {ключ: запись}, "count"}
    """
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("это не файл спектральных характеристик calc_sp_ch")
    if int(data.get("version", 0)) < 2:
        raise ValueError("файл версии 1 (объединённая геометрия) — оценок в нём нет")

    source = data.get("source") or {}
    records = {}
    for feature in data.get("features") or []:
        block = feature.get("typicality") or {}
        if block.get("value") is None:
            continue
        key = str(feature.get("key", feature.get("id")))
        record = {"value": block["value"], "source": block.get("source") or "manual"}
        if block.get("set_at"):
            record["set_at"] = block["set_at"]
        if block.get("auto"):
            record["auto"] = block["auto"]
        records[key] = record

    return {
        "vector_layer": source.get("vector_layer"),
        "id_field": source.get("id_field"),
        # в JSON поле-ключ записано явно: null означает «ключи по fid»,
        # а не «неизвестно» — в отличие от CSV
        "id_field_known": True,
        "records": records,
        "count": len(records),
    }


def load_typicality_csv(filepath):
    """
    Прочитать оценки из CSV пакетного прогона.

    Пакет выдаёт таблицу, и попытка загрузить её обратно — естественное
    ожидание, поэтому импорт понимает и её. Нужны колонки key (или fid) и
    value; разделитель определяется автоматически.
    """
    import csv

    with open(filepath, encoding="utf-8-sig", newline="") as f:
        head = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(head, delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";"
        rows = list(csv.DictReader(f, dialect=dialect))

    if not rows:
        raise ValueError("файл пустой")
    columns = set(rows[0])
    if "value" not in columns or not ({"key", "fid"} & columns):
        raise ValueError("это не таблица оценок calc_sp_ch: нужны колонки "
                         "key (или fid) и value")

    records = {}
    for row in rows:
        key = (row.get("key") or row.get("fid") or "").strip()
        raw = (row.get("value") or "").strip()
        if not key or not raw:
            continue
        try:
            value = int(float(raw))
        except ValueError:
            continue
        record = {"value": value, "source": "auto"}
        score = (row.get("score") or "").strip()
        if score:
            try:
                record["auto"] = {"score": float(score),
                                  "class_profile": (row.get("etalon") or "").strip() or None,
                                  "object_class": (row.get("class") or "").strip() or None,
                                  "from_csv": True}
            except ValueError:
                pass
        records[key] = record

    return {
        "vector_layer": None,
        "id_field": None,
        "id_field_known": False,   # в таблице поля-ключа нет — берём текущее
        "records": records,
        "count": len(records),
    }
