# -*- coding: utf-8 -*-
"""
store.py
Storage for typicality assessments.

Assessments live in project properties (QgsProject.writeEntry): they are saved
alongside the .qgz, need no editable vector layer and do not alter the source
data. The JSON export remains the source of truth for exchange, hence
to_json/from_json.

Feature key: the value of the identifier field if one is configured, otherwise
the feature id. For file-based layers the fid is a record number: it shifts if
the layer is re-saved in a different order, so an identifier field is preferable.
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
    Feature key: the id_field value (when configured and non-empty), else the fid.
    Always a string, since JSON has no non-string keys.
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
    A stable key for a layer: its source.

    QGIS regenerates layer.id() every time a layer is added to a project, so a
    reopened or recreated layer loses the settings and assessments attached to
    it. The source (the file path) survives that.
    """
    try:
        source = layer.publicSource() or layer.source() or ""
    except Exception:
        return ""
    return source.split("|")[0].strip().lower()


def _is_null(value):
    """A QVariant NULL arrives differently in different QGIS builds."""
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
    Feature assessments plus the chosen identifier field, persisted in the project.

    Record layout: {"version": 1,
                    "records":      {layer_id: {feature_key: record}},
                    "id_fields":    {layer_id: "feature key field"},
                    "class_fields": {layer_id: "feature class field"}}
    """

    PAYLOAD_VERSION = 1

    def __init__(self, project):
        self.project = project
        self._data = {}
        self._id_fields = {}
        self._class_fields = {}
        self._sources = {}          # layer_id -> source, used to carry settings over
        self.reload()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def reload(self):
        """Re-read from the project (call this after another project is opened)."""
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
        else:                       # early layout: {layer_id: {...}} at the top level
            self._data = data

    def flush(self):
        """Write into the project (which is marked as modified)."""
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
    # Binding to a layer
    # ------------------------------------------------------------------

    def remember_layer(self, layer):
        """Remember the layer source so a change of its id can be survived."""
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
        Carry settings and assessments over from an older id of the same source.

        Otherwise a reopened layer looks brand new: the class field is reset (and
        every feature becomes "no class"), the key field too, and the assessments
        already made stay stranded under the former identifier.
        Returns the id they were moved from, or None.
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
            # only carry over from a layer that is no longer in the project, or the same
            # file opened twice would pull the settings apart
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
    # Identifier field
    # ------------------------------------------------------------------

    def id_field(self, layer_id):
        """The field feature keys are built from (None - use the fid)."""
        return self._id_fields.get(layer_id) or None

    def set_id_field(self, layer_id, field_name):
        """
        Change the identifier field. Assessments already made are bound to the old
        keys, so the caller has to decide what to do with them; all that happens
        here is that the choice is stored.
        """
        if field_name:
            self._id_fields[layer_id] = str(field_name)
        else:
            self._id_fields.pop(layer_id, None)
        self.flush()
        return self.id_field(layer_id)

    def key_for(self, layer_id, feature):
        """Feature key, honouring the identifier field configured for the layer."""
        return feature_key(feature, self.id_field(layer_id))

    # ------------------------------------------------------------------
    # Class field
    # ------------------------------------------------------------------

    def class_field(self, layer_id):
        """The field holding the feature's class (None - not configured)."""
        return self._class_fields.get(layer_id) or None

    def set_class_field(self, layer_id, field_name):
        if field_name:
            self._class_fields[layer_id] = str(field_name)
        else:
            self._class_fields.pop(layer_id, None)
        self.flush()
        return self.class_field(layer_id)

    def class_of(self, layer_id, feature):
        """The feature's class field value, or None."""
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
    # Reading and writing assessments
    # ------------------------------------------------------------------

    def get(self, layer_id, key):
        """The record for a feature, or None."""
        return self._data.get(layer_id, {}).get(str(key))

    def value(self, layer_id, key):
        """Just the 1/2/3 assessment, or None."""
        record = self.get(layer_id, key)
        return normalize_value(record.get("value")) if record else None

    def set(self, layer_id, key, value, source=MANUAL, details=None, flush=True):
        """
        Set an assessment. value=None means "not set" and removes the record, so
        that empty entries do not pile up in the project.

        flush=False is for bulk writes: every flush() serializes the ENTIRE
        assessment dictionary and writes it into the project, so writing one at a
        time is quadratic (at 300 features that is already 3 ms per write, and
        growing). The caller must then call flush() itself at the end.
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
        """One assessment applied to several features. Returns how many changed."""
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
        """Clear the assessment ("not set")."""
        removed = self._data.get(layer_id, {}).pop(str(key), None)
        self._cleanup(layer_id)
        if flush:
            self.flush()
        return removed is not None

    def keys_for(self, layer_id):
        """The set of keys that carry an assessment."""
        return set(self._data.get(layer_id, {}))

    def for_layer(self, layer_id):
        """{feature_key: record} - a copy, safe to modify from outside."""
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
        """{1: n1, 2: n2, 3: n3} - a summary for the layer."""
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
    # Exchange with the JSON export
    # ------------------------------------------------------------------

    def to_json(self):
        return json.dumps(self._data, ensure_ascii=False, indent=2)

    def import_records(self, layer_id, records, overwrite=True):
        """
        Load assessments (from a previously exported JSON, for instance).
        records: {feature_key: record | value}
        Returns the number of records applied.
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
