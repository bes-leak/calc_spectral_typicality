# -*- coding: utf-8 -*-
"""
store.py
Хранилище оценок типичности.

Оценки живут в свойствах проекта (QgsProject.writeEntry): сохраняются вместе
с .qgz, не требуют редактируемого векторного слоя и не меняют исходные данные.
Источником истины при обмене остаётся JSON-экспорт — отсюда to_json/from_json.

Ключ объекта: значение поля-идентификатора, если оно задано, иначе feature id.
fid у файловых слоёв — это номер записи: он поедет, если слой пересохранить с
другой сортировкой, поэтому поле-идентификатор предпочтительнее.
"""

import json
from datetime import datetime

from . import normalize as normalize_value

SCOPE = "calc_sp_ch"
ENTRY = "typicality"

MANUAL = "manual"
AUTO = "auto"


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def feature_key(feature, id_field=None):
    """
    Ключ объекта: значение id_field (если задано и непустое) либо fid.
    Всегда строка — JSON не умеет нестроковые ключи.
    """
    if id_field:
        try:
            value = feature.attribute(id_field)
        except (KeyError, IndexError):
            value = None
        if value is not None and not _is_null(value):
            return str(value)
    return str(feature.id())


def layer_source_key(layer):
    """
    Устойчивый ключ слоя — его источник.

    layer.id() в QGIS генерируется заново при каждом добавлении слоя в проект,
    поэтому переоткрытый или пересозданный слой теряет привязанные к нему
    настройки и оценки. Источник (путь к файлу) переживает это.
    """
    try:
        source = layer.publicSource() or layer.source() or ""
    except Exception:
        return ""
    return source.split("|")[0].strip().lower()


def _is_null(value):
    """QVariant NULL приходит по-разному в разных сборках QGIS."""
    if value is None:
        return True
    is_null = getattr(value, "isNull", None)
    if callable(is_null):
        try:
            return bool(is_null())
        except Exception:
            return False
    return False


class TypicalityStore:
    """
    Оценки объектов + выбранное поле-идентификатор, с записью в проект.

    Формат записи: {"version": 1,
                    "records":      {layer_id: {feature_key: record}},
                    "id_fields":    {layer_id: "поле-ключ объекта"},
                    "class_fields": {layer_id: "поле класса объекта"}}
    """

    PAYLOAD_VERSION = 1

    def __init__(self, project):
        self.project = project
        self._data = {}
        self._id_fields = {}
        self._class_fields = {}
        self._sources = {}          # layer_id → источник, для переноса настроек
        self.reload()

    # ------------------------------------------------------------------
    # Персистентность
    # ------------------------------------------------------------------

    def reload(self):
        """Перечитать из проекта (вызывать после открытия другого проекта)."""
        self._data, self._id_fields, self._class_fields = {}, {}, {}
        self._sources = {}
        raw, ok = self.project.readEntry(SCOPE, ENTRY)
        if not ok or not raw:
            return
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        if "records" in data:
            self._data = data.get("records") or {}
            self._id_fields = data.get("id_fields") or {}
            self._class_fields = data.get("class_fields") or {}
            self._sources = data.get("sources") or {}
        else:                       # ранняя раскладка: сразу {layer_id: {...}}
            self._data = data

    def flush(self):
        """Записать в проект (проект помечается изменённым)."""
        if self._data or self._id_fields or self._class_fields:
            payload = {
                "version": self.PAYLOAD_VERSION,
                "records": self._data,
                "id_fields": self._id_fields,
                "class_fields": self._class_fields,
                "sources": self._sources,
            }
            self.project.writeEntry(SCOPE, ENTRY, json.dumps(payload, ensure_ascii=False))
        else:
            self.project.removeEntry(SCOPE, ENTRY)

    # ------------------------------------------------------------------
    # Привязка к слою
    # ------------------------------------------------------------------

    def remember_layer(self, layer):
        """Запомнить источник слоя, чтобы пережить смену его id."""
        key = layer_source_key(layer)
        if not key:
            return False
        if self._sources.get(layer.id()) == key:
            return False
        self._sources[layer.id()] = key
        self.flush()
        return True

    def adopt_layer(self, layer):
        """
        Перенести настройки и оценки со старого id того же источника.

        Иначе переоткрытый слой выглядит как совершенно новый: поле класса
        сбрасывается (и все объекты становятся «без класса»), поле-ключ тоже,
        а проставленные оценки остаются висеть под прежним идентификатором.
        Возвращает id, с которого перенесли, либо None.
        """
        layer_id, key = layer.id(), layer_source_key(layer)
        if not key:
            return None
        known = (layer_id in self._data or layer_id in self._id_fields
                 or layer_id in self._class_fields)
        if known:
            self.remember_layer(layer)
            return None

        for old_id, old_key in list(self._sources.items()):
            if old_id == layer_id or old_key != key:
                continue
            # переносим только с слоя, которого в проекте уже нет —
            # иначе один и тот же файл, открытый дважды, растащит настройки
            if self.project.mapLayer(old_id) is not None:
                continue
            if self._move_layer(old_id, layer_id, key):
                return old_id

        self.remember_layer(layer)
        return None

    def _move_layer(self, old_id, layer_id, key):
        moved = False
        for store in (self._data, self._id_fields, self._class_fields):
            if old_id in store:
                store[layer_id] = store.pop(old_id)
                moved = True
        self._sources.pop(old_id, None)
        self._sources[layer_id] = key
        if moved:
            self.flush()
        return moved

    # ------------------------------------------------------------------
    # Поле-идентификатор
    # ------------------------------------------------------------------

    def id_field(self, layer_id):
        """Поле, по которому строятся ключи объектов слоя (None — по fid)."""
        return self._id_fields.get(layer_id) or None

    def set_id_field(self, layer_id, field_name):
        """
        Сменить поле-идентификатор. Уже проставленные оценки привязаны к старым
        ключам, поэтому вызывающий код должен решить, что с ними делать —
        здесь мы только сохраняем выбор.
        """
        if field_name:
            self._id_fields[layer_id] = str(field_name)
        else:
            self._id_fields.pop(layer_id, None)
        self.flush()
        return self.id_field(layer_id)

    def key_for(self, layer_id, feature):
        """Ключ объекта с учётом настроенного для слоя поля-идентификатора."""
        return feature_key(feature, self.id_field(layer_id))

    # ------------------------------------------------------------------
    # Поле класса
    # ------------------------------------------------------------------

    def class_field(self, layer_id):
        """Поле, в котором лежит класс объекта (None — не задано)."""
        return self._class_fields.get(layer_id) or None

    def set_class_field(self, layer_id, field_name):
        if field_name:
            self._class_fields[layer_id] = str(field_name)
        else:
            self._class_fields.pop(layer_id, None)
        self.flush()
        return self.class_field(layer_id)

    def class_of(self, layer_id, feature):
        """Значение поля класса у объекта или None."""
        field = self.class_field(layer_id)
        if not field:
            return None
        try:
            value = feature.attribute(field)
        except (KeyError, IndexError):
            return None
        if value is None or _is_null(value):
            return None
        text = str(value).strip()
        return text or None

    # ------------------------------------------------------------------
    # Чтение / запись оценок
    # ------------------------------------------------------------------

    def get(self, layer_id, key):
        """Запись об объекте или None."""
        return self._data.get(layer_id, {}).get(str(key))

    def value(self, layer_id, key):
        """Только оценка 1/2/3 или None."""
        record = self.get(layer_id, key)
        return normalize_value(record.get("value")) if record else None

    def set(self, layer_id, key, value, source=MANUAL, details=None, flush=True):
        """
        Проставить оценку. value=None означает «не указано» и удаляет запись,
        чтобы в проекте не копились пустышки.

        flush=False — для пакетной записи: каждый flush() сериализует ВЕСЬ
        словарь оценок и пишет его в проект, поэтому запись по одной даёт
        квадратичное время (на 300 объектах это уже 3 мс на запись и растёт).
        Вызывающий код обязан сам позвать flush() в конце.
        """
        value = normalize_value(value)
        key = str(key)
        if value is None:
            self.clear(layer_id, key, flush=flush)
            return None

        record = {"value": value, "source": source, "set_at": _now()}
        if details:
            record["auto"] = details
        self._data.setdefault(layer_id, {})[key] = record
        if flush:
            self.flush()
        return record

    def set_many(self, layer_id, keys, value, source=MANUAL, details=None):
        """Одна оценка на несколько объектов. Возвращает число изменённых."""
        count = 0
        for key in keys:
            value_norm = normalize_value(value)
            key = str(key)
            if value_norm is None:
                if self._data.get(layer_id, {}).pop(key, None) is not None:
                    count += 1
                continue
            record = {"value": value_norm, "source": source, "set_at": _now()}
            if details:
                record["auto"] = details
            self._data.setdefault(layer_id, {})[key] = record
            count += 1
        self._cleanup(layer_id)
        self.flush()
        return count

    def clear(self, layer_id, key, flush=True):
        """Убрать оценку («не указано»)."""
        removed = self._data.get(layer_id, {}).pop(str(key), None)
        self._cleanup(layer_id)
        if flush:
            self.flush()
        return removed is not None

    def keys_for(self, layer_id):
        """Множество ключей, под которыми есть оценки."""
        return set(self._data.get(layer_id, {}))

    def for_layer(self, layer_id):
        """{feature_key: record} — копия, менять снаружи безопасно."""
        return dict(self._data.get(layer_id, {}))

    def remove_layer(self, layer_id):
        removed = self._data.pop(layer_id, None) is not None
        removed = self._id_fields.pop(layer_id, None) is not None or removed
        removed = self._class_fields.pop(layer_id, None) is not None or removed
        self._sources.pop(layer_id, None)
        if removed:
            self.flush()
        return removed

    def counts(self, layer_id):
        """{1: n1, 2: n2, 3: n3} — сводка по слою."""
        result = {}
        for record in self._data.get(layer_id, {}).values():
            value = normalize_value(record.get("value"))
            if value is not None:
                result[value] = result.get(value, 0) + 1
        return result

    def _cleanup(self, layer_id):
        if layer_id in self._data and not self._data[layer_id]:
            del self._data[layer_id]

    # ------------------------------------------------------------------
    # Обмен с JSON-экспортом
    # ------------------------------------------------------------------

    def to_json(self):
        return json.dumps(self._data, ensure_ascii=False, indent=2)

    def import_records(self, layer_id, records, overwrite=True):
        """
        Загрузить оценки (например из ранее выгруженного JSON).
        records: {feature_key: record | value}
        Возвращает число применённых записей.
        """
        target = self._data.setdefault(layer_id, {})
        count = 0
        for key, record in (records or {}).items():
            key = str(key)
            if not overwrite and key in target:
                continue
            if not isinstance(record, dict):
                record = {"value": record, "source": MANUAL, "set_at": _now()}
            if normalize_value(record.get("value")) is None:
                continue
            record = dict(record)
            record["value"] = normalize_value(record["value"])
            target[key] = record
            count += 1
        self._cleanup(layer_id)
        self.flush()
        return count
