# -*- coding: utf-8 -*-
"""
vector_utils.py
Vector feature helpers: identifiers and reprojection of geometry into the raster CRS.
"""

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
)


class CrsTransformError(RuntimeError):
    """Could not reproject the geometry into the raster coordinate system."""


def get_feature_ids(features):
    result = []
    for feature in features:
        result.append(feature.id())
    return ' '.join(map(str, result))


def _categorized_renderer(layer):
    """The layer renderer if it is a categorized one, otherwise None."""
    try:
        renderer = layer.renderer()
    except Exception:
        return None
    return renderer if isinstance(renderer, QgsCategorizedSymbolRenderer) else None


def categorized_class_field(layer):
    """
    The field the layer is coloured by in the layer tree.

    If the user has already styled the layer by class, the classification field
    is settled and there is no point asking for it a second time. Expressions
    are ignored: a feature's class must be readable from an attribute.
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
    {field value: category label} taken from the layer legend.

    Legend labels are usually shorter and clearer than the raw values, so they
    are what the interface shows. Class profiles are still matched by value:
    a label can be edited at any time, a value is stable.
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
    [(feature, geometry in dst_crs)] for features with non-empty geometry.

    A reprojection failure is NOT swallowed: carrying on with the original
    coordinates would silently sample a different place on the map.
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
                    raise CrsTransformError("QgsGeometry.transform() reported an error")
            except CrsTransformError:
                raise
            except Exception as e:
                raise CrsTransformError(
                    f"feature {feature.id()}: {src_crs.authid()} → "
                    f"{dst_crs.authid()}: {e}") from e
        result.append((feature, geom))
    return result


def geometry_in_crs(geom, src_crs, dst_crs, project=None):
    """A single geometry from one CRS to another. None if it could not be reprojected."""
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
    """The union of the geometries, or None."""
    geoms = [g for g in geoms if g is not None and not g.isEmpty()]
    if not geoms:
        return None
    union = QgsGeometry.unaryUnion(geoms)
    if union is None or union.isEmpty():
        return None
    return union
