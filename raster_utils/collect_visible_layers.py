from qgis.core import QgsLayerTreeLayer   # not qgis._core: that module is private


def get_visible_raster_layers(project):
    """
    Return only the raster layers that are currently visible.
    """
    visible_layers = []
    root = project.layerTreeRoot()

    def collect_visible_rasters(node):
        """Recursively collect visible rasters"""
        if not node.isVisible():  # Node is hidden, skip the whole group
            return

        if isinstance(node, QgsLayerTreeLayer):
            layer = node.layer()
            # Check: raster + visible + valid
            if layer and layer.type() == 1 and layer.isValid():
                visible_layers.append(layer)

        # Recurse into child nodes (groups)
        for child in node.children():
            collect_visible_rasters(child)

    collect_visible_rasters(root)
    return visible_layers