# -*- coding: utf-8 -*-
"""
vector_utils.py
Работа с векторными объектами: идентификаторы и перевод геометрии в CRS растра.
"""

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
)


class CrsTransformError(RuntimeError):
    """Не удалось перевести геометрию в систему координат растра."""


def get_feature_ids(features):
    result = []
    for feature in features:
        result.append(feature.id())
    return ' '.join(map(str, result))


def _categorized_renderer(layer):
    """Рендерер слоя, если он «Категоризованный», иначе None."""
    try:
        renderer = layer.renderer()
    except Exception:
        return None
    return renderer if isinstance(renderer, QgsCategorizedSymbolRenderer) else None


def categorized_class_field(layer):
    """
    Поле, по которому слой раскрашен в дереве слоёв.

    Если пользователь стилизовал слой по классам, то поле классификации уже
    выбрано — незачем просить выбрать его второй раз. Выражения не берём:
    класс объекта должен читаться из атрибута.
    """
    renderer = _categorized_renderer(layer)
    if renderer is None:
        return None
    attribute = (renderer.classAttribute() or "").strip()
    if not attribute:
        return None
    try:
        names = {field.name() for field in layer.fields()}
    except Exception:
        return None
    return attribute if attribute in names else None


def category_labels(layer):
    """
    {значение поля: подпись категории} из легенды слоя.

    Подписи в дереве слоёв обычно короче и понятнее самих значений, поэтому
    показываем в интерфейсе их. Сопоставление эталонов при этом идёт
    по значению — подпись можно поменять в любой момент, значение стабильно.
    """
    renderer = _categorized_renderer(layer)
    if renderer is None:
        return {}
    labels = {}
    for category in renderer.categories():
        value = category.value()
        if value is None:
            continue
        key = str(value).strip()
        label = (category.label() or "").strip()
        if key and label and label != key:
            labels[key] = label
    return labels


def geometries_in_crs(features, src_crs, dst_crs, project=None):
    """
    [(feature, geometry в dst_crs)] для объектов с непустой геометрией.

    Ошибку перепроекции НЕ проглатываем: продолжать с исходными координатами
    нельзя — выборка молча уедет в другое место карты.
    """
    project = project or QgsProject.instance()
    transform = None
    if src_crs != dst_crs:
        try:
            transform = QgsCoordinateTransform(src_crs, dst_crs, project)
        except Exception as e:
            raise CrsTransformError(
                f"{src_crs.authid()} → {dst_crs.authid()}: {e}") from e

    result = []
    for feature in features:
        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            continue
        geom = QgsGeometry(geom)
        if transform is not None:
            try:
                if geom.transform(transform) != 0:
                    raise CrsTransformError("QgsGeometry.transform() вернул ошибку")
            except CrsTransformError:
                raise
            except Exception as e:
                raise CrsTransformError(
                    f"объект {feature.id()}: {src_crs.authid()} → "
                    f"{dst_crs.authid()}: {e}") from e
        result.append((feature, geom))
    return result


def geometry_in_crs(geom, src_crs, dst_crs, project=None):
    """Одна геометрия из одной CRS в другую. None, если перевести не удалось."""
    if geom is None or geom.isEmpty():
        return None
    if src_crs == dst_crs or not src_crs.isValid() or not dst_crs.isValid():
        return QgsGeometry(geom)
    project = project or QgsProject.instance()
    try:
        transform = QgsCoordinateTransform(src_crs, dst_crs, project)
        result = QgsGeometry(geom)
        if result.transform(transform) != 0:
            return None
        return result
    except Exception:
        return None


def union_geometry(geoms):
    """Объединение геометрий или None."""
    geoms = [g for g in geoms if g is not None and not g.isEmpty()]
    if not geoms:
        return None
    union = QgsGeometry.unaryUnion(geoms)
    if union is None or union.isEmpty():
        return None
    return union
