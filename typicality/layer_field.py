# -*- coding: utf-8 -*-
"""
Mirroring typicality assessments into an attribute of the vector layer.

The assessments themselves live in the project (see store.py), which keeps the
user's data untouched. That is the right default, but it makes the values hard
to use: you cannot filter, style or label by something QGIS does not know is an
attribute. This module writes them into a real field as well, so the layer can
be queried like any other.

Writing goes through the provider when the layer is not being edited, and
through the edit buffer when it is, so an open edit session is neither bypassed
nor committed behind the user's back.
"""
from qgis.core import QgsField, QgsVectorDataProvider
from qgis.PyQt.QtCore import QVariant

from .store import feature_key

#: Ten characters exactly, which is the limit a shapefile allows.
DEFAULT_FIELD = "typicality"


class FieldWriteError(Exception):
    """The layer cannot take the field, with a reason fit to show the user."""


def _has(provider, capability):
    flag = getattr(QgsVectorDataProvider, capability, None)
    if flag is None:
        return True              # unknown build: let the actual call decide
    return bool(provider.capabilities() & flag)


def can_write(layer):
    """(ok, reason) - whether assessments can be mirrored into this layer."""
    provider = layer.dataProvider() if layer is not None else None
    if provider is None:
        return False, "the layer has no data provider"
    if not _has(provider, "ChangeAttributeValues"):
        return False, f"the layer \"{layer.name()}\" is read-only"
    if not _has(provider, "AddAttributes"):
        # an existing field of our own would still be writable
        return True, ""
    return True, ""


def find_field(layer, name=DEFAULT_FIELD):
    """The actual field name, or None. Matching is case-insensitive."""
    wanted = (name or "").strip().lower()
    for field in layer.fields():
        if field.name().lower() == wanted:
            return field.name()
    return None


def ensure_field(layer, name=DEFAULT_FIELD):
    """
    Return the name of the field to write into, creating it when missing.

    A provider may store the field under a slightly different name - a
    shapefile truncates anything past ten characters - so the name that comes
    back is the one the layer actually has, not the one that was asked for.
    """
    ok, reason = can_write(layer)
    if not ok:
        raise FieldWriteError(reason)

    existing = find_field(layer, name)
    if existing is not None:
        index = layer.fields().indexOf(existing)
        if not layer.fields().at(index).isNumeric():
            raise FieldWriteError(
                f"the field \"{existing}\" already exists and is not numeric")
        return existing

    provider = layer.dataProvider()
    if not _has(provider, "AddAttributes"):
        raise FieldWriteError(
            f"the layer \"{layer.name()}\" does not allow new fields")
    if layer.isEditable():
        raise FieldWriteError(
            f"the layer \"{layer.name()}\" is in edit mode; save or discard the "
            "changes first, then the field can be added")

    if not provider.addAttributes([QgsField(name, QVariant.Int)]):
        raise FieldWriteError(
            f"the provider refused to add the field \"{name}\"")
    layer.updateFields()

    created = find_field(layer, name) or find_field(layer, name[:10])
    if created is None:
        raise FieldWriteError(
            f"the field \"{name}\" was added but cannot be found on the layer")
    return created


def _null():
    """A typed NULL, so clearing a value does not write a zero."""
    return QVariant(QVariant.Int)


def _differs(current, value):
    if current is None or (hasattr(current, "isNull") and current.isNull()):
        return value is not None
    try:
        return int(current) != value if value is not None else True
    except (TypeError, ValueError):
        return True


def apply_changes(layer, index, changes):
    """
    Write {fid: value|None} into attribute `index`. Returns how many changed.

    Everything goes through the layer's edit buffer. Writing to the provider
    directly would leave the layer's own feature cache stale, and refreshing it
    with reload() drops the selection - which, right after assessing the
    selected feature, is exactly the wrong thing to do.

    An edit session the user opened is left open: the changes join their undo
    stack and they decide when to commit. One we opened ourselves is committed
    immediately, so nothing is left dangling.
    """
    if not changes:
        return 0

    ours = not layer.isEditable()
    if ours and not layer.startEditing():
        raise FieldWriteError(
            f"the layer \"{layer.name()}\" could not be opened for editing")

    written = 0
    try:
        for fid, value in changes.items():
            payload = _null() if value is None else int(value)
            if layer.changeAttributeValue(fid, index, payload):
                written += 1
    except Exception:
        if ours:
            layer.rollBack()
        raise

    if ours and not layer.commitChanges():
        errors = "; ".join(layer.commitErrors()[:3])
        layer.rollBack()
        raise FieldWriteError(
            f"the changes to \"{layer.name()}\" could not be saved: {errors}")
    return written


def sync_layer(layer, store, name=None):
    """
    Bring the whole layer's attribute in line with the stored assessments.

    Features with no assessment are set to NULL rather than left alone: a stale
    value from an earlier run is worse than an empty cell.
    Returns (field name, number of features changed).
    """
    field = ensure_field(layer, name or store.target_field(layer.id())
                         or DEFAULT_FIELD)
    index = layer.fields().indexOf(field)
    id_field = store.id_field(layer.id())

    changes = {}
    for feature in layer.getFeatures():
        value = store.value(layer.id(), feature_key(feature, id_field))
        if _differs(feature.attributes()[index], value):
            changes[feature.id()] = value
    return field, apply_changes(layer, index, changes)


def sync_features(layer, store, values_by_fid, name=None):
    """
    Write a known set of {fid: value} without walking the whole layer.

    The batch run and the single-feature assessment both already know which
    features they touched, and a layer of a hundred thousand features should not
    be scanned to write three of them.
    """
    field = ensure_field(layer, name or store.target_field(layer.id())
                         or DEFAULT_FIELD)
    index = layer.fields().indexOf(field)
    return field, apply_changes(layer, index, dict(values_by_fid))
