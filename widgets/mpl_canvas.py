# -*- coding: utf-8 -*-
"""
Matplotlib canvas for the plot area of the dock widget.

Matplotlib ships with the OSGeo4W/Windows builds of QGIS, but not with every
Linux distribution package, so the import is guarded: without it the plugin
still loads and everything except the plots keeps working. MATPLOTLIB_ERROR
holds the reason, for the message shown in place of the plot.
"""

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
except ImportError as exc:                              # pragma: no cover
    FigureCanvas = object
    Figure = None
    MATPLOTLIB_ERROR = str(exc)
else:
    MATPLOTLIB_ERROR = None


class MplCanvas(FigureCanvas):
    """
    Thin wrapper around a Matplotlib FigureCanvas.
    Keeps Matplotlib creation out of the plugin controller.
    """

    def __init__(self, width=5, height=4, dpi=100, parent=None):
        if MATPLOTLIB_ERROR is not None:                 # pragma: no cover
            raise ImportError(MATPLOTLIB_ERROR)
        self.figure = Figure(figsize=(width, height), dpi=dpi)
        super().__init__(self.figure)
        if parent is not None:
            self.setParent(parent)

    def new_axes(self):
        self.figure.clear()
        return self.figure.add_subplot(111)
