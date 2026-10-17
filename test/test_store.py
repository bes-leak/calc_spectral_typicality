# -*- coding: utf-8 -*-
"""The assessment store and the per-layer settings kept beside it."""
import unittest

from .support import FakeProject, store_mod

TypicalityStore = store_mod.TypicalityStore
LAYER = "layer_1"


class Feature:
    """The slice of QgsFeature that feature_key reads."""

    def __init__(self, fid, attributes=None):
        self._id = fid
        self._attributes = attributes or {}

    def id(self):
        return self._id

    def attribute(self, name):
        if name not in self._attributes:
            raise KeyError(name)
        return self._attributes[name]


class AssessmentTest(unittest.TestCase):

    def setUp(self):
        self.project = FakeProject()
        self.store = TypicalityStore(self.project)

    def test_setting_and_reading_back(self):
        self.store.set(LAYER, "A-1", 1, source="manual")
        self.assertEqual(self.store.value(LAYER, "A-1"), 1)
        self.assertEqual(self.store.get(LAYER, "A-1")["source"], "manual")

    def test_keys_are_strings_whatever_was_passed(self):
        self.store.set(LAYER, 7, 2)
        self.assertEqual(self.store.value(LAYER, "7"), 2)
        self.assertEqual(self.store.value(LAYER, 7), 2)

    def test_clearing_removes_the_record_rather_than_storing_a_blank(self):
        self.store.set(LAYER, "A-1", 3)
        self.store.set(LAYER, "A-1", None)
        self.assertIsNone(self.store.value(LAYER, "A-1"))
        self.assertEqual(self.store.for_layer(LAYER), {})

    def test_counts_summarize_the_layer(self):
        self.store.set_many(LAYER, ["a", "b", "c"], 2)
        self.store.set(LAYER, "d", 3)
        self.assertEqual(self.store.counts(LAYER), {2: 3, 3: 1})

    def test_survives_a_reload_of_the_project(self):
        self.store.set(LAYER, "A-1", 1, details={"score": 0.9})
        reopened = TypicalityStore(self.project)
        self.assertEqual(reopened.value(LAYER, "A-1"), 1)
        self.assertEqual(reopened.get(LAYER, "A-1")["auto"]["score"], 0.9)

    def test_bulk_writes_can_defer_the_flush(self):
        for i in range(5):
            self.store.set(LAYER, f"k{i}", 1, flush=False)
        self.assertEqual(TypicalityStore(self.project).counts(LAYER), {})
        self.store.flush()
        self.assertEqual(TypicalityStore(self.project).counts(LAYER), {1: 5})


class FeatureKeyTest(unittest.TestCase):

    def test_the_fid_is_the_default_key(self):
        self.assertEqual(store_mod.feature_key(Feature(12)), "12")

    def test_an_identifier_field_wins_over_the_fid(self):
        feature = Feature(12, {"obj_id": "A-01"})
        self.assertEqual(store_mod.feature_key(feature, "obj_id"), "A-01")

    def test_an_empty_or_missing_field_falls_back_to_the_fid(self):
        self.assertEqual(store_mod.feature_key(Feature(12, {"obj_id": ""}), "obj_id"), "12")
        self.assertEqual(store_mod.feature_key(Feature(12), "no_such"), "12")

    def test_a_numeric_field_is_turned_into_a_string(self):
        self.assertEqual(store_mod.feature_key(Feature(12, {"num": 10}), "num"), "10")


class PerLayerSettingsTest(unittest.TestCase):

    def setUp(self):
        self.project = FakeProject()
        self.store = TypicalityStore(self.project)

    def test_identifier_and_class_fields_are_remembered(self):
        self.store.set_id_field(LAYER, "obj_id")
        self.store.set_class_field(LAYER, "type_name")
        reopened = TypicalityStore(self.project)
        self.assertEqual(reopened.id_field(LAYER), "obj_id")
        self.assertEqual(reopened.class_field(LAYER), "type_name")

    def test_the_target_field_is_off_until_it_is_set(self):
        self.assertIsNone(self.store.target_field(LAYER))

    def test_the_target_field_is_remembered_and_can_be_cleared(self):
        self.store.set_target_field(LAYER, "typicality")
        self.assertEqual(TypicalityStore(self.project).target_field(LAYER),
                         "typicality")
        self.store.set_target_field(LAYER, None)
        self.assertIsNone(TypicalityStore(self.project).target_field(LAYER))

    def test_the_target_field_alone_is_enough_to_be_persisted(self):
        """It must survive even when nothing has been assessed yet."""
        self.store.set_target_field(LAYER, "typicality")
        self.assertEqual(self.store.counts(LAYER), {})
        self.assertEqual(TypicalityStore(self.project).target_field(LAYER),
                         "typicality")

    def test_a_project_written_before_target_fields_existed_still_loads(self):
        self.store.set(LAYER, "A-1", 1)
        self.store.set_id_field(LAYER, "obj_id")
        raw, _ = self.project.readEntry(store_mod.SCOPE, store_mod.ENTRY)
        import json
        payload = json.loads(raw)
        payload.pop("target_fields", None)          # the 0.2.0 layout
        self.project.writeEntry(store_mod.SCOPE, store_mod.ENTRY,
                                json.dumps(payload))
        reopened = TypicalityStore(self.project)
        self.assertEqual(reopened.value(LAYER, "A-1"), 1)
        self.assertEqual(reopened.id_field(LAYER), "obj_id")
        self.assertIsNone(reopened.target_field(LAYER))

    def test_class_of_reads_the_configured_field(self):
        self.store.set_class_field(LAYER, "type_name")
        feature = Feature(1, {"type_name": "pine"})
        self.assertEqual(self.store.class_of(LAYER, feature), "pine")
        self.assertIsNone(self.store.class_of(LAYER, Feature(2, {"type_name": "  "})))


if __name__ == "__main__":
    unittest.main()
