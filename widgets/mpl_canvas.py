from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class MplCanvas(FigureCanvas):
    """
    Thin wrapper around a Matplotlib FigureCanvas.
    Keeps Matplotlib creation out of the plugin controller.
    """

    def __init__(self, width=5, height=4, dpi=100, parent=None):
        self.figure = Figure(figsize=(width, height), dpi=dpi)
        super().__init__(self.figure)
        if parent is not None:
            self.setParent(parent)

    def new_axes(self):
        self.figure.clear()
        return self.figure.add_subplot(111)

