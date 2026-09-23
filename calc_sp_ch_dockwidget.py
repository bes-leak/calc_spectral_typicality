# -*- coding: utf-8 -*-
import os

from qgis.PyQt import QtWidgets, uic
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QButtonGroup, QComboBox, QGridLayout, QGroupBox, QLabel,
    QPushButton, QRadioButton,
)

from .typicality import LABELS

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'calc_sp_ch_dockwidget_base.ui'))

#: 0 in the radio group means "not set": pyqtSignal(int) cannot carry None
UNSET = 0


class SpectralCharacteristicsDockWidget(QtWidgets.QDockWidget, FORM_CLASS):

    closingPlugin   = pyqtSignal()
    plotTypeChanged = pyqtSignal(str)
    samplingModeChanged = pyqtSignal(str)
    rasterChanged   = pyqtSignal(str)         # id of the selected raster layer
    analyzeClicked  = pyqtSignal()
    exportJsonClicked = pyqtSignal()          # <- new signal

    # --- typicality ---
    typicalityChanged = pyqtSignal(int)       # 1..3, 0 = not set
    idFieldChanged = pyqtSignal(str)          # '' = key taken from the fid
    classFieldChanged = pyqtSignal(str)       # '' = no class field configured
    profileSelected = pyqtSignal(str)         # another profile was picked in the list
    etalonRequested = pyqtSignal()            # turn the selection into a class profile
    etalonSaveRequested = pyqtSignal()
    etalonLoadRequested = pyqtSignal()
    etalonDeleteRequested = pyqtSignal()
    etalonRecalcRequested = pyqtSignal()
    importTypicalityRequested = pyqtSignal()
    autoTypicalityRequested = pyqtSignal()
    batchTypicalityRequested = pyqtSignal()
    separabilityRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)

        # Raster to analyse - the first visible raster used to be taken silently
        self.comboBox_raster = QComboBox(self.widget_plot)
        self.comboBox_raster.setToolTip("The raster layer the feature spectra are computed from")
        self.comboBox_raster.currentIndexChanged.connect(self._on_raster_changed)

        # Sampling mode
        self.comboBox_sampling_mode.addItems(['polygon', 'bbox'])
        self.comboBox_sampling_mode.setToolTip(
            "polygon - only pixels inside the feature; bbox - the whole feature extent")
        self.comboBox_sampling_mode.currentTextChanged.connect(self._on_sampling_mode_changed)

        # Plot type
        self.comboBox_plot_type = QComboBox(self.widget_plot)
        self.comboBox_plot_type.addItems(['bar', 'line', 'step'])
        self.comboBox_plot_type.setToolTip("Distribution plot type")
        self.comboBox_plot_type.currentTextChanged.connect(self.plotTypeChanged.emit)

        # Analyze button
        self.btn_analyze = QPushButton("🔍 Analyse", self.widget_plot)
        self.btn_analyze.setToolTip("Plot the spectrum of the selected feature")
        self.btn_analyze.clicked.connect(self.analyzeClicked.emit)

        # Export JSON button  <- new button
        self.btn_export_json = QPushButton("💾 JSON", self.widget_plot)
        self.btn_export_json.setToolTip(
            "Export the feature characteristics to JSON:\n"
            "outline, centroid, statistics inside and outside, typicality assessment"
        )
        self.btn_export_json.clicked.connect(self.exportJsonClicked.emit)

        # Keep the plot control row short: the plot itself needs the space
        for widget in (self.comboBox_raster, self.comboBox_sampling_mode,
                       self.comboBox_plot_type, self.btn_analyze, self.btn_export_json):
            widget.setMaximumHeight(24)

        self.group_typicality = self._build_typicality_group()

        layout = self.widget_plot.layout()
        if layout:
            if isinstance(layout, QGridLayout):
                # Row 0: raster selector (full width)
                layout.addWidget(self.comboBox_raster, 0, 0, 1, 4)
                # Row 1: modes and plot buttons on a single line, so they do not
                # eat into the height of the plot
                layout.addWidget(self.comboBox_sampling_mode, 1, 0, 1, 1)
                layout.addWidget(self.comboBox_plot_type,     1, 1, 1, 1)
                layout.addWidget(self.btn_analyze,            1, 2, 1, 1)
                layout.addWidget(self.btn_export_json,        1, 3, 1, 1)
                # Row 2: typicality
                layout.addWidget(self.group_typicality, 2, 0, 1, 4)
                # Row 3: canvas (added by plugin)
            else:
                for index, widget in enumerate((
                        self.comboBox_raster, self.comboBox_sampling_mode,
                        self.comboBox_plot_type, self.btn_analyze,
                        self.btn_export_json, self.group_typicality)):
                    layout.insertWidget(index, widget)

    # ------------------------------------------------------------------
    # The "Typicality" group
    # ------------------------------------------------------------------

    def _build_typicality_group(self):
        group = QGroupBox("Typicality", self.widget_plot)
        grid = QGridLayout(group)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setVerticalSpacing(4)

        # --- class profile library ---
        self.comboBox_profile = QComboBox(group)
        self.comboBox_profile.setToolTip(
            "Class profiles. Automatic assessment picks the profile from the\n"
            "feature class field; picking one here overrides that by hand")
        self.comboBox_profile.currentIndexChanged.connect(self._on_profile_selected)

        self.btn_make_etalon = QPushButton("★", group)
        self.btn_make_etalon.setToolTip(
            "Create or update a class profile from the selected features:\n"
            "mean spectrum, covariance and per-band histograms")
        self.btn_save_etalon = QPushButton("💾", group)
        self.btn_save_etalon.setToolTip("Save the class profiles to a file")
        self.btn_load_etalon = QPushButton("📂", group)
        self.btn_load_etalon.setToolTip("Load class profile(s) from a file")
        # ✕ rather than 🗑: some Windows fonts draw non-BMP characters as a box
        self.btn_recalc_etalon = QPushButton("↻", group)
        self.btn_recalc_etalon.setToolTip(
            "Recompute every class profile against the currently selected raster:\n"
            "the outlines stay, the statistics are taken from the new image")
        self.btn_delete_etalon = QPushButton("✕", group)
        self.btn_delete_etalon.setToolTip("Delete the selected class profile")
        for button in (self.btn_make_etalon, self.btn_save_etalon,
                       self.btn_load_etalon, self.btn_recalc_etalon,
                       self.btn_delete_etalon):
            button.setMaximumWidth(30)

        self.btn_make_etalon.clicked.connect(self.etalonRequested.emit)
        self.btn_save_etalon.clicked.connect(self.etalonSaveRequested.emit)
        self.btn_load_etalon.clicked.connect(self.etalonLoadRequested.emit)
        self.btn_recalc_etalon.clicked.connect(self.etalonRecalcRequested.emit)
        self.btn_delete_etalon.clicked.connect(self.etalonDeleteRequested.emit)

        grid.addWidget(QLabel("profile:", group), 0, 0, 1, 1)
        grid.addWidget(self.comboBox_profile, 0, 1, 1, 1)
        grid.addWidget(self.btn_make_etalon, 0, 2, 1, 1)
        grid.addWidget(self.btn_save_etalon, 0, 3, 1, 1)
        grid.addWidget(self.btn_load_etalon, 0, 4, 1, 1)
        grid.addWidget(self.btn_recalc_etalon, 0, 5, 1, 1)
        grid.addWidget(self.btn_delete_etalon, 0, 6, 1, 1)

        # --- profile summary and class field ---
        self.label_etalon = QLabel("No class profiles", group)
        self.label_etalon.setWordWrap(True)
        self.comboBox_class_field = QComboBox(group)
        self.comboBox_class_field.setToolTip(
            "The field holding the feature class (type_name, for instance).\n"
            "Automatic assessment uses it to pick a profile for every feature")
        self.comboBox_class_field.currentIndexChanged.connect(self._on_class_field_changed)

        grid.addWidget(self.label_etalon, 1, 0, 1, 4)
        grid.addWidget(QLabel("class:", group), 1, 4, 1, 1)
        grid.addWidget(self.comboBox_class_field, 1, 5, 1, 2)

        # --- feature and key ---
        self.label_selection = QLabel("Select a single feature", group)
        self.label_selection.setWordWrap(True)
        self.comboBox_id_field = QComboBox(group)
        self.comboBox_id_field.setToolTip(
            "The feature identifier field.\n"
            "By default the key is the fid, but that changes when the layer is\n"
            "re-saved, so a stable field is safer.\n"
            "Values must be unique, otherwise assessments overwrite one another")
        self.comboBox_id_field.currentIndexChanged.connect(self._on_id_field_changed)

        grid.addWidget(self.label_selection, 2, 0, 1, 4)
        grid.addWidget(QLabel("key:", group), 2, 4, 1, 1)
        grid.addWidget(self.comboBox_id_field, 2, 5, 1, 2)

        # --- assessment ---
        self.radio_group = QButtonGroup(group)
        self.radio_buttons = {}
        # A 2x2 grid rather than one row: in a row the labels take ~500 px and
        # push up the minimum width of the whole panel, starving the plot.
        radio_grid = QGridLayout()
        radio_grid.setContentsMargins(0, 0, 0, 0)
        for index, value in enumerate((1, 2, 3, UNSET)):
            text = f"{value} — {LABELS[value]}" if value else LABELS[None]
            button = QRadioButton(text, group)
            self.radio_group.addButton(button, value)
            self.radio_buttons[value] = button
            radio_grid.addWidget(button, index // 2, index % 2)
        radio_grid.setColumnStretch(2, 1)          # push to the left edge
        self.radio_buttons[UNSET].setChecked(True)
        # buttonClicked rather than toggled: setting the value programmatically
        # must not look like a user action
        self.radio_group.buttonClicked.connect(self._on_typicality_clicked)
        grid.addLayout(radio_grid, 3, 0, 1, 7)

        # --- automatic assessment and import ---
        self.btn_auto_typicality = QPushButton("⚙ Auto-assess", group)
        self.btn_auto_typicality.setToolTip(
            "Assess typicality from similarity metrics against the profile:\n"
            "spectral angle, Mahalanobis, share of similar pixels")
        self.btn_auto_typicality.clicked.connect(self.autoTypicalityRequested.emit)

        self.btn_batch_typicality = QPushButton("⚙ Batch over layer", group)
        self.btn_batch_typicality.setToolTip(
            "Assess every feature whose class has a profile.\n"
            "The settings come from the single-feature dialog")
        self.btn_batch_typicality.clicked.connect(self.batchTypicalityRequested.emit)

        self.btn_import_typicality = QPushButton("⬇ Import assessments", group)
        self.btn_import_typicality.setToolTip(
            "Take typicality assessments from a previously exported JSON")
        self.btn_import_typicality.clicked.connect(self.importTypicalityRequested.emit)

        self.btn_separability = QPushButton("▦ Separability matrix", group)
        self.btn_separability.setToolTip(
            "Run the features against EVERY profile at once and export a\n"
            "\"feature class x profile\" table. Assessments in the project are left alone")
        self.btn_separability.clicked.connect(self.separabilityRequested.emit)

        grid.addWidget(self.btn_auto_typicality, 4, 0, 1, 4)
        grid.addWidget(self.btn_batch_typicality, 4, 4, 1, 3)
        grid.addWidget(self.btn_separability, 5, 0, 1, 4)
        grid.addWidget(self.btn_import_typicality, 5, 4, 1, 3)

        grid.setColumnStretch(1, 1)
        self.set_selection_info("Select a single feature", False)
        return group

    # --- external state control ---

    def set_etalon_info(self, text):
        self.label_etalon.setText(text)

    def set_selection_info(self, text, enabled, auto_enabled=None, batch_enabled=None):
        """The selection caption plus the availability of the assessment controls."""
        self.label_selection.setText(text)
        for button in self.radio_buttons.values():
            button.setEnabled(enabled)
        self.btn_auto_typicality.setEnabled(enabled if auto_enabled is None else auto_enabled)
        # the batch and the matrix do not depend on a single feature being
        # selected, only on profiles being present
        state = bool(batch_enabled) if batch_enabled is not None else True
        self.btn_batch_typicality.setEnabled(state)
        self.btn_separability.setEnabled(state)

    def set_typicality(self, value):
        """Show an assessment without raising the signal."""
        button = self.radio_buttons.get(value or UNSET)
        if button is not None:
            button.setChecked(True)

    def set_id_field_options(self, field_names, current=None):
        """Fill the identifier field list, preserving the current choice."""
        return self._fill_field_combo(self.comboBox_id_field, field_names, current, "fid")

    def set_class_field_options(self, field_names, current=None):
        """Fill the class field list."""
        return self._fill_field_combo(self.comboBox_class_field, field_names, current,
                                      "(not set)")

    @staticmethod
    def _fill_field_combo(combo, field_names, current, empty_title):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(empty_title, "")
        for name in field_names:
            combo.addItem(name, name)
        index = combo.findData(current or "")
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)
        combo.setEnabled(bool(field_names))
        return combo.currentData()

    def set_profile_options(self, options, current=None):
        """
        Fill the class profile list, preserving the current choice. No signal is
        raised. options: [(label, class name)], or just [class name].
        """
        options = [(o, o) if isinstance(o, str) else tuple(o) for o in options]
        names = [name for _, name in options]
        combo = self.comboBox_profile
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for title, name in options:
            combo.addItem(title, name)
            # the full class value goes into the tooltip: labels can be short, while
            # matching happens by value
            combo.setItemData(combo.count() - 1, name, Qt.ToolTipRole)
        if names:
            index = combo.findData(current if current is not None else previous)
            combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)
        combo.setEnabled(bool(names))
        self.btn_delete_etalon.setEnabled(bool(names))
        self.btn_save_etalon.setEnabled(bool(names))
        return combo.currentData()

    def current_profile(self):
        return self.comboBox_profile.currentData() or ""

    def current_id_field(self):
        return self.comboBox_id_field.currentData() or ""

    def current_class_field(self):
        return self.comboBox_class_field.currentData() or ""

    def _on_id_field_changed(self, _index):
        self.idFieldChanged.emit(self.current_id_field())

    def _on_class_field_changed(self, _index):
        self.classFieldChanged.emit(self.current_class_field())

    def _on_profile_selected(self, _index):
        name = self.current_profile()
        if name:
            self.profileSelected.emit(name)

    def _on_typicality_clicked(self, button):
        self.typicalityChanged.emit(self.radio_group.id(button))

    # ------------------------------------------------------------------
    # Raster selector
    # ------------------------------------------------------------------

    def set_raster_options(self, options):
        """
        options: list of (label, layer_id).
        Preserves the current choice if that layer is still listed. NO signal is
        emitted; the resulting value is returned to the caller instead.
        """
        combo = self.comboBox_raster
        previous = combo.currentData()

        combo.blockSignals(True)
        combo.clear()
        for label, layer_id in options:
            combo.addItem(label, layer_id)
        if options:
            index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

        combo.setEnabled(bool(options))
        return combo.currentData()

    def current_raster_id(self):
        return self.comboBox_raster.currentData()

    def _on_raster_changed(self, _index):
        layer_id = self.comboBox_raster.currentData()
        if layer_id:
            self.rasterChanged.emit(layer_id)

    def _on_sampling_mode_changed(self, text):
        self.samplingModeChanged.emit(text)

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()