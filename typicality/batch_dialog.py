# -*- coding: utf-8 -*-
"""
batch_dialog.py
The launch window for a batch assessment: what will be processed and with which
settings.

The point of the window is to show in advance how many features of which classes
the run will cover, and which classes have no profile. Otherwise a batch over a
layer of hundreds of features is a lottery.
"""

from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox, QLabel,
    QVBoxLayout,
)

from .metrics import METRIC_ORDER, MODES


class BatchRunDialog(QDialog):
    """Confirmation dialog for starting a batch assessment."""

    def __init__(self, class_stats, scope_text, settings, parent=None,
                 has_manual=0):
        """
        class_stats: [(class, features, has_profile)]
        settings:    {"weights", "thresholds", "mode"}
        has_manual:  how many features are already assessed by hand
        """
        super().__init__(parent)
        self.setWindowTitle("Batch typicality assessment")
        self.class_stats = list(class_stats)

        layout = QVBoxLayout(self)

        ready = sum(count for _, count, ok in self.class_stats if ok)
        missing = sum(count for _, count, ok in self.class_stats if not ok)
        header = QLabel(f"{scope_text}\n"
                        f"Will be assessed: {ready}"
                        + (f", skipped for want of a profile: {missing}" if missing else ""),
                        self)
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)

        layout.addWidget(self._build_classes())
        layout.addWidget(self._build_settings(settings))

        self.check_overwrite_manual = QCheckBox(
            f"Overwrite assessments made by hand ({has_manual})", self)
        self.check_overwrite_manual.setToolTip(
            "By default manual work is left alone: the batch only refreshes\n"
            "automatic assessments and fills in the empty ones")
        self.check_overwrite_manual.setEnabled(has_manual > 0)
        layout.addWidget(self.check_overwrite_manual)

        self.check_csv = QCheckBox("Save the results as CSV", self)
        self.check_csv.setChecked(True)
        layout.addWidget(self.check_csv)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("Run")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Ok).setEnabled(ready > 0)
        layout.addWidget(buttons)

    def _build_classes(self):
        group = QGroupBox("Classes", self)
        grid = QGridLayout(group)
        grid.addWidget(QLabel("<b>Class</b>", group), 0, 0)
        grid.addWidget(QLabel("<b>Features</b>", group), 0, 1)
        grid.addWidget(QLabel("<b>Profile</b>", group), 0, 2)
        for row, (name, count, has_profile) in enumerate(self.class_stats, start=1):
            grid.addWidget(QLabel(name or "- no class -", group), row, 0)
            grid.addWidget(QLabel(str(count), group), row, 1)
            mark = QLabel("yes" if has_profile else "no", group)
            if not has_profile:
                mark.setStyleSheet("color: #a94442;")
            grid.addWidget(mark, row, 2)
        grid.setColumnStretch(0, 1)
        return group

    def _build_settings(self, settings):
        group = QGroupBox("Calculation settings", self)
        layout = QVBoxLayout(group)
        weights = settings.get("weights") or {}
        active = ", ".join(f"{key} {weights[key]:g}" for key in METRIC_ORDER
                           if weights.get(key))
        high, low = (settings.get("thresholds") or (0.66, 0.33))[:2]
        mode = MODES.get(settings.get("mode", "sum"), settings.get("mode", "sum"))
        text = QLabel(f"Metrics: {active or '—'}\n"
                      f"Thresholds: 1 when score >= {high:g}, 2 when score >= {low:g}\n"
                      f"Aggregation: {mode}", group)
        text.setWordWrap(True)
        text.setToolTip("The settings come from the single-feature dialog:\n"
                        "tune them there on one feature, then run the batch")
        layout.addWidget(text)
        return group

    # ------------------------------------------------------------------

    def overwrite_manual(self):
        return self.check_overwrite_manual.isChecked()

    def want_csv(self):
        return self.check_csv.isChecked()
