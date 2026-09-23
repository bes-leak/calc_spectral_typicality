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

#: 0 в радио-группе означает «не указано» — pyqtSignal(int) не умеет None
UNSET = 0


class SpectralCharecteristicsDockWidget(QtWidgets.QDockWidget, FORM_CLASS):

    closingPlugin   = pyqtSignal()
    plotTypeChanged = pyqtSignal(str)
    samplingModeChanged = pyqtSignal(str)
    rasterChanged   = pyqtSignal(str)         # id выбранного растрового слоя
    analyzeClicked  = pyqtSignal()
    exportJsonClicked = pyqtSignal()          # ← новый сигнал

    # --- типичность ---
    typicalityChanged = pyqtSignal(int)       # 1..3, 0 = не указано
    idFieldChanged = pyqtSignal(str)          # '' = ключ по fid
    classFieldChanged = pyqtSignal(str)       # '' = класс не задан
    profileSelected = pyqtSignal(str)         # выбран другой эталон в списке
    etalonRequested = pyqtSignal()            # сделать эталоном выделенное
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

        # Raster to analyse — раньше молча брался первый видимый растр
        self.comboBox_raster = QComboBox(self.widget_plot)
        self.comboBox_raster.setToolTip("Растровый слой, по которому считается спектр объектов")
        self.comboBox_raster.currentIndexChanged.connect(self._on_raster_changed)

        # Sampling mode
        self.comboBox_sampling_mode.addItems(['polygon', 'bbox'])
        self.comboBox_sampling_mode.setToolTip(
            "polygon — только пиксели внутри объекта; bbox — весь охват объекта")
        self.comboBox_sampling_mode.currentTextChanged.connect(self._on_sampling_mode_changed)

        # Plot type
        self.comboBox_plot_type = QComboBox(self.widget_plot)
        self.comboBox_plot_type.addItems(['bar', 'line', 'step'])
        self.comboBox_plot_type.setToolTip("Тип графика распределения")
        self.comboBox_plot_type.currentTextChanged.connect(self.plotTypeChanged.emit)

        # Analyze button
        self.btn_analyze = QPushButton("🔍 Анализ", self.widget_plot)
        self.btn_analyze.setToolTip("Построить спектр выделенного объекта")
        self.btn_analyze.clicked.connect(self.analyzeClicked.emit)

        # Export JSON button  ← новая кнопка
        self.btn_export_json = QPushButton("💾 JSON", self.widget_plot)
        self.btn_export_json.setToolTip(
            "Выгрузить характеристики объектов в JSON:\n"
            "контур, центроид, статистика внутри и снаружи, оценка типичности"
        )
        self.btn_export_json.clicked.connect(self.exportJsonClicked.emit)

        # Ряд управления графиком держим низким — место нужно самому графику
        for widget in (self.comboBox_raster, self.comboBox_sampling_mode,
                       self.comboBox_plot_type, self.btn_analyze, self.btn_export_json):
            widget.setMaximumHeight(24)

        self.group_typicality = self._build_typicality_group()

        layout = self.widget_plot.layout()
        if layout:
            if isinstance(layout, QGridLayout):
                # Row 0: raster selector (full width)
                layout.addWidget(self.comboBox_raster, 0, 0, 1, 4)
                # Row 1: режимы и кнопки графика — одной строкой, чтобы не
                # съедать высоту у самого графика
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
    # Группа «Типичность»
    # ------------------------------------------------------------------

    def _build_typicality_group(self):
        group = QGroupBox("Типичность", self.widget_plot)
        grid = QGridLayout(group)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setVerticalSpacing(4)

        # --- библиотека эталонов ---
        self.comboBox_profile = QComboBox(group)
        self.comboBox_profile.setToolTip(
            "Эталоны классов. При автоопределении эталон подбирается по полю\n"
            "класса объекта; выбор здесь — ручное переопределение")
        self.comboBox_profile.currentIndexChanged.connect(self._on_profile_selected)

        self.btn_make_etalon = QPushButton("★", group)
        self.btn_make_etalon.setToolTip(
            "Создать или обновить эталон класса по выделенным объектам:\n"
            "средний спектр, ковариация и гистограммы каналов")
        self.btn_save_etalon = QPushButton("💾", group)
        self.btn_save_etalon.setToolTip("Сохранить эталоны в файл")
        self.btn_load_etalon = QPushButton("📂", group)
        self.btn_load_etalon.setToolTip("Загрузить эталон(ы) из файла")
        # ✕, а не 🗑: символы вне BMP на части шрифтов Windows рисуются квадратом
        self.btn_recalc_etalon = QPushButton("↻", group)
        self.btn_recalc_etalon.setToolTip(
            "Пересчитать все эталоны по выбранному сейчас растру:\n"
            "контуры те же, статистика берётся с нового снимка")
        self.btn_delete_etalon = QPushButton("✕", group)
        self.btn_delete_etalon.setToolTip("Удалить выбранный эталон")
        for button in (self.btn_make_etalon, self.btn_save_etalon,
                       self.btn_load_etalon, self.btn_recalc_etalon,
                       self.btn_delete_etalon):
            button.setMaximumWidth(30)

        self.btn_make_etalon.clicked.connect(self.etalonRequested.emit)
        self.btn_save_etalon.clicked.connect(self.etalonSaveRequested.emit)
        self.btn_load_etalon.clicked.connect(self.etalonLoadRequested.emit)
        self.btn_recalc_etalon.clicked.connect(self.etalonRecalcRequested.emit)
        self.btn_delete_etalon.clicked.connect(self.etalonDeleteRequested.emit)

        grid.addWidget(QLabel("эталон:", group), 0, 0, 1, 1)
        grid.addWidget(self.comboBox_profile, 0, 1, 1, 1)
        grid.addWidget(self.btn_make_etalon, 0, 2, 1, 1)
        grid.addWidget(self.btn_save_etalon, 0, 3, 1, 1)
        grid.addWidget(self.btn_load_etalon, 0, 4, 1, 1)
        grid.addWidget(self.btn_recalc_etalon, 0, 5, 1, 1)
        grid.addWidget(self.btn_delete_etalon, 0, 6, 1, 1)

        # --- сводка по эталону и поле класса ---
        self.label_etalon = QLabel("Эталонов нет", group)
        self.label_etalon.setWordWrap(True)
        self.comboBox_class_field = QComboBox(group)
        self.comboBox_class_field.setToolTip(
            "Поле с классом объекта (например type_name).\n"
            "По нему автоопределение подбирает эталон для каждого объекта")
        self.comboBox_class_field.currentIndexChanged.connect(self._on_class_field_changed)

        grid.addWidget(self.label_etalon, 1, 0, 1, 4)
        grid.addWidget(QLabel("класс:", group), 1, 4, 1, 1)
        grid.addWidget(self.comboBox_class_field, 1, 5, 1, 2)

        # --- объект и ключ ---
        self.label_selection = QLabel("Выделите один объект", group)
        self.label_selection.setWordWrap(True)
        self.comboBox_id_field = QComboBox(group)
        self.comboBox_id_field.setToolTip(
            "Поле-идентификатор объекта.\n"
            "По умолчанию ключом служит fid, но он меняется при пересохранении\n"
            "слоя — стабильное поле надёжнее.\n"
            "Значения должны быть уникальными, иначе оценки затрут друг друга")
        self.comboBox_id_field.currentIndexChanged.connect(self._on_id_field_changed)

        grid.addWidget(self.label_selection, 2, 0, 1, 4)
        grid.addWidget(QLabel("ключ:", group), 2, 4, 1, 1)
        grid.addWidget(self.comboBox_id_field, 2, 5, 1, 2)

        # --- оценка ---
        self.radio_group = QButtonGroup(group)
        self.radio_buttons = {}
        # Сетка 2x2, а не один ряд: в ряд подписи занимают ~500 px и задирают
        # минимальную ширину всей панели, отбирая место у графика.
        radio_grid = QGridLayout()
        radio_grid.setContentsMargins(0, 0, 0, 0)
        for index, value in enumerate((1, 2, 3, UNSET)):
            text = f"{value} — {LABELS[value]}" if value else LABELS[None]
            button = QRadioButton(text, group)
            self.radio_group.addButton(button, value)
            self.radio_buttons[value] = button
            radio_grid.addWidget(button, index // 2, index % 2)
        radio_grid.setColumnStretch(2, 1)          # прижать к левому краю
        self.radio_buttons[UNSET].setChecked(True)
        # buttonClicked, а не toggled: программная установка не должна
        # выглядеть как действие пользователя
        self.radio_group.buttonClicked.connect(self._on_typicality_clicked)
        grid.addLayout(radio_grid, 3, 0, 1, 7)

        # --- автоопределение и импорт ---
        self.btn_auto_typicality = QPushButton("⚙ Автоопределение", group)
        self.btn_auto_typicality.setToolTip(
            "Оценить типичность по метрикам сходства с эталоном:\n"
            "спектральный угол, Махаланобис, доля похожих пикселей")
        self.btn_auto_typicality.clicked.connect(self.autoTypicalityRequested.emit)

        self.btn_batch_typicality = QPushButton("⚙ Пакет по слою", group)
        self.btn_batch_typicality.setToolTip(
            "Оценить все объекты, у чьего класса есть эталон.\n"
            "Настройки берутся из окна одиночного автоопределения")
        self.btn_batch_typicality.clicked.connect(self.batchTypicalityRequested.emit)

        self.btn_import_typicality = QPushButton("⬇ Импорт оценок", group)
        self.btn_import_typicality.setToolTip(
            "Взять оценки типичности из ранее выгруженного JSON")
        self.btn_import_typicality.clicked.connect(self.importTypicalityRequested.emit)

        self.btn_separability = QPushButton("▦ Матрица разделимости", group)
        self.btn_separability.setToolTip(
            "Прогнать объекты по ВСЕМ эталонам сразу и выгрузить таблицу\n"
            "«класс объектов × эталон». Оценки в проекте не меняются")
        self.btn_separability.clicked.connect(self.separabilityRequested.emit)

        grid.addWidget(self.btn_auto_typicality, 4, 0, 1, 4)
        grid.addWidget(self.btn_batch_typicality, 4, 4, 1, 3)
        grid.addWidget(self.btn_separability, 5, 0, 1, 4)
        grid.addWidget(self.btn_import_typicality, 5, 4, 1, 3)

        grid.setColumnStretch(1, 1)
        self.set_selection_info("Выделите один объект", False)
        return group

    # --- внешнее управление состоянием ---

    def set_etalon_info(self, text):
        self.label_etalon.setText(text)

    def set_selection_info(self, text, enabled, auto_enabled=None, batch_enabled=None):
        """Подпись про выделение + доступность оценок."""
        self.label_selection.setText(text)
        for button in self.radio_buttons.values():
            button.setEnabled(enabled)
        self.btn_auto_typicality.setEnabled(enabled if auto_enabled is None else auto_enabled)
        # пакет и матрица не зависят от выделения одного объекта —
        # только от наличия эталонов
        state = bool(batch_enabled) if batch_enabled is not None else True
        self.btn_batch_typicality.setEnabled(state)
        self.btn_separability.setEnabled(state)

    def set_typicality(self, value):
        """Показать оценку, не поднимая сигнал."""
        button = self.radio_buttons.get(value or UNSET)
        if button is not None:
            button.setChecked(True)

    def set_id_field_options(self, field_names, current=None):
        """Заполнить список полей-идентификаторов, сохранив выбор."""
        return self._fill_field_combo(self.comboBox_id_field, field_names, current, "fid")

    def set_class_field_options(self, field_names, current=None):
        """Заполнить список полей класса."""
        return self._fill_field_combo(self.comboBox_class_field, field_names, current,
                                      "(не задан)")

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
        Заполнить список эталонов, сохранив выбор. Сигнал не поднимается.
        options: [(подпись, имя класса)] либо просто [имя класса].
        """
        options = [(o, o) if isinstance(o, str) else tuple(o) for o in options]
        names = [name for _, name in options]
        combo = self.comboBox_profile
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for title, name in options:
            combo.addItem(title, name)
            # полное значение класса — в подсказке: подписи бывают короткими,
            # а сопоставление идёт именно по значению
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
        Сохраняет текущий выбор, если слой ещё в списке. Сигнал НЕ шлётся —
        актуальное значение возвращается вызывающему.
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