import os
import tempfile
import time
import uuid
from typing import Optional

from qgis.PyQt import sip
from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

MASK_PREFIX = "calc_sp_ch_mask_"
_SIDECAR_SUFFIXES = ("", ".aux.xml", ".ovr")


def mask_dir() -> str:
    """Отдельная папка во временном каталоге — чтобы уборка не трогала чужое."""
    path = os.path.join(tempfile.gettempdir(), "calc_sp_ch_masks")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return tempfile.gettempdir()
    return path


def remove_mask_file(path: Optional[str]) -> bool:
    """Удалить файл маски и его спутники. True, если основной файл удалён."""
    if not path:
        return False
    base = path.split('|')[0]          # источник может быть с |layername=...
    if os.path.basename(base)[:len(MASK_PREFIX)] != MASK_PREFIX:
        return False                   # чужой файл — не трогаем
    removed = False
    for suffix in _SIDECAR_SUFFIXES:
        try:
            os.remove(base + suffix)
            removed = removed or suffix == ""
        except OSError:
            pass
    return removed


def cleanup_stale_masks(max_age_hours: float = 24.0) -> int:
    """
    Удалить маски, оставшиеся от прошлых сессий (например после падения QGIS).
    Файлы, ещё занятые процессом, просто не удалятся — это не ошибка.
    """
    directory = mask_dir()
    deadline = time.time() - max_age_hours * 3600
    count = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(MASK_PREFIX):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) > deadline:
                continue
            os.remove(path)
            count += 1
        except OSError:
            pass
    return count


def _create_single_feature_layer(geom: QgsGeometry, crs_authid: str) -> QgsVectorLayer:
    vl = QgsVectorLayer(f"Polygon?crs={crs_authid}", "analysis_polygon", "memory")
    pr = vl.dataProvider()
    f = QgsFeature()
    f.setGeometry(geom)
    pr.addFeatures([f])
    vl.updateExtents()
    return vl


def _style_mask_layer(mask_layer: QgsRasterLayer, color: str = "#00ffff", opacity: float = 0.35):
    from qgis.core import (
        QgsPalettedRasterRenderer,
    )

    mask_layer.setOpacity(opacity)

    # Используем палитровый рендерер: 0 → прозрачный, 1 → нужный цвет
    classes = [
        QgsPalettedRasterRenderer.Class(0, QColor(0, 0, 0, 0), "No data"),
        QgsPalettedRasterRenderer.Class(1, QColor(color), "Mask"),
    ]
    renderer = QgsPalettedRasterRenderer(mask_layer.dataProvider(), 1, classes)
    mask_layer.setRenderer(renderer)
    mask_layer.triggerRepaint()


def create_polygon_mask_raster(
    project: QgsProject,
    raster_layer: QgsRasterLayer,
    geom: QgsGeometry,
    name: str = "polygon_mask",
) -> Optional[QgsRasterLayer]:
    """
    Create a byte mask raster aligned to `raster_layer` (1 inside geom, 0 outside),
    load it as a raster layer and return it.

    Uses Processing (GDAL rasterize) for performance on real imagery.
    Returns None if processing is unavailable/fails.
    """
    if raster_layer is None or sip.isdeleted(raster_layer):
        return None
    if not raster_layer.isValid():
        return None
    try:
        import processing  # type: ignore
    except Exception:
        return None

    if geom is None or geom.isEmpty():
        return None

    crs_authid = raster_layer.crs().authid()
    vlayer = _create_single_feature_layer(geom, crs_authid)

    extent = raster_layer.extent()
    x_res = raster_layer.rasterUnitsPerPixelX()
    y_res = raster_layer.rasterUnitsPerPixelY()

    out_path = os.path.join(
        mask_dir(), f"{MASK_PREFIX}{name}_{os.getpid()}_{uuid.uuid4().hex[:8]}.tif"
    )

    params = {
        "INPUT": vlayer,
        "FIELD": None,
        "BURN": 1,
        "USE_Z": False,
        "UNITS": 1,  # Georeferenced units
        "WIDTH": x_res,
        "HEIGHT": y_res,
        "EXTENT": f"{extent.xMinimum()},{extent.xMaximum()},{extent.yMinimum()},{extent.yMaximum()}",
        "NODATA": 0,
        "DATA_TYPE": 0,  # Byte
        "INIT": 0,
        "INVERT": False,
        "OUTPUT": out_path,
    }

    # Some QGIS/GDAL versions use slightly different param names;
    # keep a best-effort try and fall back on EXTRA if needed.
    try:
        processing.run("gdal:rasterize", params)
    except Exception:
        try:
            # Conservative fallback: pass rasterize options via EXTRA
            extra = (
                f"-te {extent.xMinimum()} {extent.yMinimum()} {extent.xMaximum()} {extent.yMaximum()} "
                f"-tr {x_res} {y_res} -burn 1 -a_nodata 0 -ot Byte -init 0"
            )
            params2 = {
                "INPUT": vlayer,
                "FIELD": None,
                "BURN": 1,
                "EXTRA": extra,
                "OUTPUT": out_path,
            }
            processing.run("gdal:rasterize", params2)
        except Exception:
            remove_mask_file(out_path)
            return None

    mask_layer = QgsRasterLayer(out_path, name, "gdal")
    if not mask_layer.isValid():
        remove_mask_file(out_path)
        return None

    _style_mask_layer(mask_layer)
    project.addMapLayer(mask_layer, addToLegend=False)
    root = project.layerTreeRoot()
    node = root.insertLayer(-1, mask_layer)  # в конец
    node.setItemVisibilityChecked(False)  # скрыт по умолчанию
    return mask_layer

