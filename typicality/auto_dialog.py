# -*- coding: utf-8 -*-
"""
auto_dialog.py
Диалог автоопределения типичности: метрики, веса, пороги, разложение вердикта.

Метрики считаются ОДИН раз при открытии (это дорогая часть — проход по всем
пикселям объекта), дальше изменение весов, порогов и способа свёртки меняет
только агрегацию, поэтому окно откликается мгновенно.
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
    """Показывает расчёт и возвращает выбранную оценку."""

    def __init__(self, profile, candidate, parent=None, weights=None,
                 thresholds=None, mode=MODE_SUM, object_label=""):
        super().__init__(parent)
        self.setWindowTitle("Автоопределение типичности")
        self.profile = profile
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self.thresholds = tuple(thresholds or DEFAULT_THRESHOLDS)
        self.result = None

        # дорогая часть — один раз
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
        buttons.button(QDialogButtonBox.Ok).setText("Применить оценку")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.button_ok = buttons.button(QDialogButtonBox.Ok)

        self.recalculate()

    # ------------------------------------------------------------------
    # Сборка
    # ------------------------------------------------------------------

    def _build_header(self, object_label):
        text = (f"Эталон «{self.profile.name}»: {self.profile.n_pixels} пикс., "
                f"{self.profile.k} канал(ов)\n"
                f"Объект {object_label}: {self.candidate.n} пикс.")
        label = QLabel(text, self)
        label.setStyleSheet("font-weight: bold;")
        return label

    def _build_metrics_table(self):
        group = QGroupBox("Метрики", self)
        grid = QGridLayout(group)
        grid.setVerticalSpacing(3)

        headers = ("Метрика", "Вес", "Значение", "Похожесть", "Вклад")
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
        group = QGroupBox("Пороги и свёртка", self)
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

        grid.addWidget(QLabel("1 — типичный при score ≥", group), 0, 0)
        grid.addWidget(self.spin_high, 0, 1)
        grid.addWidget(QLabel("2 — сомнительный при score ≥", group), 1, 0)
        grid.addWidget(self.spin_low, 1, 1)

        self.combo_mode = QComboBox(group)
        for key, title in MODES.items():
            self.combo_mode.addItem(title, key)
        index = self.combo_mode.findData(mode)
        self.combo_mode.setCurrentIndex(index if index >= 0 else 0)
        self.combo_mode.setToolTip(
            "Сумма: метрики компенсируют друг друга.\n"
            "Произведение: достаточно одной метрики «против», чтобы уронить оценку.")
        self.combo_mode.currentIndexChanged.connect(self.recalculate)
        grid.addWidget(QLabel("Свёртка:", group), 2, 0)
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
    # Расчёт
    # ------------------------------------------------------------------

    def current_weights(self):
        return {key: (self.spinboxes[key].value() if self.checkboxes[key].isChecked() else 0.0)
                for key in METRIC_ORDER}

    def current_thresholds(self):
        high, low = self.spin_high.value(), self.spin_low.value()
        if low > high:                      # не даём порогам перепутаться местами
            low = high
        return high, low

    def current_mode(self):
        return self.combo_mode.currentData() or MODE_SUM

    def _on_toggle(self, _state):
        for key, checkbox in self.checkboxes.items():
            self.spinboxes[key].setEnabled(checkbox.isChecked())
        self.recalculate()

    def recalculate(self):
        """Пересобрать вердикт из уже посчитанных метрик."""
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
            self.label_verdict.setText("Оценка не посчитана — включите хотя бы одну метрику")
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
        """Настройки, которые стоит запомнить до следующего вызова."""
        return {"weights": dict(self.weights),
                "thresholds": list(self.thresholds),
                "mode": self.current_mode()}
