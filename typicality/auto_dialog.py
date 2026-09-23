# -*- coding: utf-8 -*-
"""
auto_dialog.py
The automatic typicality dialog: metrics, weights, thresholds and a breakdown of
the verdict.

The metrics are computed ONCE when the window opens (that is the expensive part,
a pass over every pixel of the feature); after that, changing weights,
thresholds or the aggregation mode only redoes the aggregation, so the window
responds instantly.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout,
)

from . import label_for
from .metrics import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    METRIC_GROUPS,
    METRIC_ORDER,
    MODE_SUM,
    MODES,
    aggregate,
    run_metrics,
)

_DASH = "—"


class AutoTypicalityDialog(QDialog):
    """Shows the calculation and returns the chosen assessment."""

    def __init__(self, profile, candidate, parent=None, weights=None,
                 thresholds=None, mode=MODE_SUM, object_label=""):
        super().__init__(parent)
        self.setWindowTitle("Automatic typicality")
        self.profile = profile
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self.thresholds = tuple(thresholds or DEFAULT_THRESHOLDS)
        self.result = None

        # the expensive part, done once
        self.metric_results, self.base_notes, self.candidate = run_metrics(profile, candidate)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_header(object_label))
        layout.addWidget(self._build_metrics_table())
        layout.addWidget(self._build_settings(mode))
        layout.addWidget(self._build_verdict())

        self.label_notes = QLabel(self)
        self.label_notes.setWordWrap(True)
        self.label_notes.setStyleSheet("color: #8a6d3b;")
        layout.addWidget(self.label_notes)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("Apply assessment")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.button_ok = buttons.button(QDialogButtonBox.Ok)

        self.recalculate()

    # ------------------------------------------------------------------
    # Building the UI
    # ------------------------------------------------------------------

    def _build_header(self, object_label):
        text = (f"Profile \"{self.profile.name}\": {self.profile.n_pixels} px, "
                f"{self.profile.k} band(s)\n"
                f"Feature {object_label}: {self.candidate.n} px")
        label = QLabel(text, self)
        label.setStyleSheet("font-weight: bold;")
        return label

    def _build_metrics_table(self):
        group = QGroupBox("Metrics", self)
        grid = QGridLayout(group)
        grid.setVerticalSpacing(3)

        headers = ("Metric", "Weight", "Value", "Similarity", "Contribution")
        for column, title in enumerate(headers):
            header = QLabel(f"<b>{title}</b>", group)
            grid.addWidget(header, 0, column)

        self.checkboxes, self.spinboxes = {}, {}
        self.labels_raw, self.labels_similarity, self.labels_contribution = {}, {}, {}
        self.labels_note = {}

        for row, key in enumerate(METRIC_ORDER, start=1):
            result = self.metric_results[key]
            weight = float(self.weights.get(key, 0.0) or 0.0)

            checkbox = QCheckBox(f"{result.name} · {METRIC_GROUPS[key]}", group)
            checkbox.setChecked(weight > 0 and result.ok)
            checkbox.setEnabled(result.ok)
            checkbox.stateChanged.connect(self._on_toggle)

            spinbox = QDoubleSpinBox(group)
            spinbox.setRange(0.0, 1.0)
            spinbox.setSingleStep(0.05)
            spinbox.setDecimals(2)
            spinbox.setValue(weight if weight > 0 else 0.25)
            spinbox.setEnabled(checkbox.isChecked())
            spinbox.valueChanged.connect(self.recalculate)

            self.checkboxes[key], self.spinboxes[key] = checkbox, spinbox
            self.labels_raw[key] = QLabel(_DASH, group)
            self.labels_similarity[key] = QLabel(_DASH, group)
            self.labels_contribution[key] = QLabel(_DASH, group)

            grid.addWidget(checkbox, row * 2 - 1, 0)
            grid.addWidget(spinbox, row * 2 - 1, 1)
            grid.addWidget(self.labels_raw[key], row * 2 - 1, 2)
            grid.addWidget(self.labels_similarity[key], row * 2 - 1, 3)
            grid.addWidget(self.labels_contribution[key], row * 2 - 1, 4)

            note = QLabel(result.note, group)
            note.setStyleSheet("color: gray; font-size: 10px;")
            note.setWordWrap(True)
            self.labels_note[key] = note
            grid.addWidget(note, row * 2, 0, 1, 5)

        grid.setColumnStretch(0, 1)
        return group

    def _build_settings(self, mode):
        group = QGroupBox("Thresholds and aggregation", self)
        grid = QGridLayout(group)

        self.spin_high = QDoubleSpinBox(group)
        self.spin_low = QDoubleSpinBox(group)
        for spin, value in ((self.spin_high, self.thresholds[0]),
                            (self.spin_low, self.thresholds[1])):
            spin.setRange(0.0, 1.0)
            spin.setSingleStep(0.01)
            spin.setDecimals(2)
            spin.setValue(value)
            spin.valueChanged.connect(self.recalculate)

        grid.addWidget(QLabel("1 - typical when score >=", group), 0, 0)
        grid.addWidget(self.spin_high, 0, 1)
        grid.addWidget(QLabel("2 - doubtful when score >=", group), 1, 0)
        grid.addWidget(self.spin_low, 1, 1)

        self.combo_mode = QComboBox(group)
        for key, title in MODES.items():
            self.combo_mode.addItem(title, key)
        index = self.combo_mode.findData(mode)
        self.combo_mode.setCurrentIndex(index if index >= 0 else 0)
        self.combo_mode.setToolTip(
            "Sum: the metrics compensate for one another.\n"
            "Product: one dissenting metric is enough to sink the assessment.")
        self.combo_mode.currentIndexChanged.connect(self.recalculate)
        grid.addWidget(QLabel("Aggregation:", group), 2, 0)
        grid.addWidget(self.combo_mode, 2, 1)
        grid.setColumnStretch(0, 1)
        return group

    def _build_verdict(self):
        frame = QFrame(self)
        frame.setFrameShape(QFrame.StyledPanel)
        row = QHBoxLayout(frame)
        self.label_verdict = QLabel(_DASH, frame)
        self.label_verdict.setAlignment(Qt.AlignCenter)
        self.label_verdict.setStyleSheet("font-size: 14px; font-weight: bold;")
        row.addWidget(self.label_verdict)
        return frame

    # ------------------------------------------------------------------
    # Calculation
    # ------------------------------------------------------------------

    def current_weights(self):
        return {key: (self.spinboxes[key].value() if self.checkboxes[key].isChecked() else 0.0)
                for key in METRIC_ORDER}

    def current_thresholds(self):
        high, low = self.spin_high.value(), self.spin_low.value()
        if low > high:                      # keep the thresholds from swapping places
            low = high
        return high, low

    def current_mode(self):
        return self.combo_mode.currentData() or MODE_SUM

    def _on_toggle(self, _state):
        for key, checkbox in self.checkboxes.items():
            self.spinboxes[key].setEnabled(checkbox.isChecked())
        self.recalculate()

    def recalculate(self):
        """Rebuild the verdict from the metrics already computed."""
        self.weights = self.current_weights()
        self.thresholds = self.current_thresholds()
        self.result = aggregate(self.metric_results, self.weights, self.thresholds,
                                self.current_mode(), self.base_notes)

        contributions = self.result.contributions()
        for key in METRIC_ORDER:
            result = self.metric_results[key]
            raw = _DASH if result.raw is None else f"{result.raw:.4g} {result.unit}".strip()
            similarity = _DASH if not result.ok else f"{result.similarity:.3f}"
            contribution = contributions.get(key)
            self.labels_raw[key].setText(raw)
            self.labels_similarity[key].setText(similarity)
            self.labels_contribution[key].setText(
                _DASH if contribution is None else f"{contribution:.3f}")
            self.labels_note[key].setText(result.note)

        if self.result.score is None:
            self.label_verdict.setText("No assessment computed - enable at least one metric")
            self.button_ok.setEnabled(False)
        else:
            self.label_verdict.setText(
                f"score = {self.result.score:.3f}  →  "
                f"{self.result.value} ({label_for(self.result.value)})")
            self.button_ok.setEnabled(True)

        self.label_notes.setText("\n".join(f"⚠ {note}" for note in self.result.notes))
        return self.result

    # ------------------------------------------------------------------

    def settings(self):
        """The settings worth remembering until the next invocation."""
        return {"weights": dict(self.weights),
                "thresholds": list(self.thresholds),
                "mode": self.current_mode()}
