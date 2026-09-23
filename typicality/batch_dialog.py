# -*- coding: utf-8 -*-
"""
batch_dialog.py
Окно запуска пакетной оценки: что будет обработано и с какими настройками.

Смысл окна — показать заранее, сколько объектов и каких классов попадёт в
прогон и у каких классов эталона нет. Иначе пакет по слою из сотен объектов
превращается в лотерею.
"""

from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox, QLabel,
    QVBoxLayout,
)

from .metrics import METRIC_ORDER, MODES


class BatchRunDialog(QDialog):
    """Подтверждение запуска пакетной оценки."""

    def __init__(self, class_stats, scope_text, settings, parent=None,
                 has_manual=0):
        """
        class_stats: [(класс, объектов, есть_эталон)]
        settings:    {"weights", "thresholds", "mode"}
        has_manual:  сколько объектов уже размечено вручную
        """
        super().__init__(parent)
        self.setWindowTitle("Пакетная оценка типичности")
        self.class_stats = list(class_stats)

        layout = QVBoxLayout(self)

        ready = sum(count for _, count, ok in self.class_stats if ok)
        missing = sum(count for _, count, ok in self.class_stats if not ok)
        header = QLabel(f"{scope_text}\n"
                        f"Будет оценено: {ready}"
                        + (f", пропущено без эталона: {missing}" if missing else ""),
                        self)
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)

        layout.addWidget(self._build_classes())
        layout.addWidget(self._build_settings(settings))

        self.check_overwrite_manual = QCheckBox(
            f"Перезаписывать оценки, проставленные вручную ({has_manual})", self)
        self.check_overwrite_manual.setToolTip(
            "По умолчанию ручная разметка не трогается — пакет обновляет только\n"
            "автоматические оценки и заполняет пустые")
        self.check_overwrite_manual.setEnabled(has_manual > 0)
        layout.addWidget(self.check_overwrite_manual)

        self.check_csv = QCheckBox("Сохранить CSV с результатами", self)
        self.check_csv.setChecked(True)
        layout.addWidget(self.check_csv)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("Запустить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Ok).setEnabled(ready > 0)
        layout.addWidget(buttons)

    def _build_classes(self):
        group = QGroupBox("Классы", self)
        grid = QGridLayout(group)
        grid.addWidget(QLabel("<b>Класс</b>", group), 0, 0)
        grid.addWidget(QLabel("<b>Объектов</b>", group), 0, 1)
        grid.addWidget(QLabel("<b>Эталон</b>", group), 0, 2)
        for row, (name, count, has_profile) in enumerate(self.class_stats, start=1):
            grid.addWidget(QLabel(name or "— без класса —", group), row, 0)
            grid.addWidget(QLabel(str(count), group), row, 1)
            mark = QLabel("есть" if has_profile else "нет", group)
            if not has_profile:
                mark.setStyleSheet("color: #a94442;")
            grid.addWidget(mark, row, 2)
        grid.setColumnStretch(0, 1)
        return group

    def _build_settings(self, settings):
        group = QGroupBox("Настройки расчёта", self)
        layout = QVBoxLayout(group)
        weights = settings.get("weights") or {}
        active = ", ".join(f"{key} {weights[key]:g}" for key in METRIC_ORDER
                           if weights.get(key))
        high, low = (settings.get("thresholds") or (0.66, 0.33))[:2]
        mode = MODES.get(settings.get("mode", "sum"), settings.get("mode", "sum"))
        text = QLabel(f"Метрики: {active or '—'}\n"
                      f"Пороги: 1 при score ≥ {high:g}, 2 при score ≥ {low:g}\n"
                      f"Свёртка: {mode}", group)
        text.setWordWrap(True)
        text.setToolTip("Настройки берутся из окна одиночного автоопределения —\n"
                        "подберите их там на одном объекте, затем гоните пакет")
        layout.addWidget(text)
        return group

    # ------------------------------------------------------------------

    def overwrite_manual(self):
        return self.check_overwrite_manual.isChecked()

    def want_csv(self):
        return self.check_csv.isChecked()
