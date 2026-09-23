from qgis.core import QgsLayerTreeLayer   # не qgis._core: это приватный модуль


def get_visible_raster_layers(project):
    """
    Возвращает список только видимых растровых слоёв
    """
    visible_layers = []
    root = project.layerTreeRoot()

    def collect_visible_rasters(node):
        """Рекурсивный сбор видимых растров"""
        if not node.isVisible():  # Слой скрыт — пропускаем всю группу
            return

        if isinstance(node, QgsLayerTreeLayer):
            layer = node.layer()
            # Проверяем: растр + виден + валиден
            if layer and layer.type() == 1 and layer.isValid():
                visible_layers.append(layer)

        # Рекурсивно обрабатываем дочерние элементы (группы)
        for child in node.children():
            collect_visible_rasters(child)

    collect_visible_rasters(root)
    return visible_layers