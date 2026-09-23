# -*- coding: utf-8 -*-
"""
The batch run and the separability matrix.

Neither touches QGIS: geometry and raster access arrive as callbacks, which is
exactly what makes them testable here.
"""
import csv
import os
import tempfile
import unittest

import numpy as np

from .support import SIGNATURE, batch_mod, make_profile, sample

CLASSES = {
    "pine":   SIGNATURE,
    "spruce": SIGNATURE + np.array([400.0, -300.0, 200.0, -800.0]),
    "swamp":  SIGNATURE + np.array([-500.0, 700.0, -300.0, 1200.0]),
}


class Feature:
    """The slice of QgsFeature that run_batch uses: just an id."""

    def __init__(self, fid, class_name):
        self._id = fid
        self.class_name = class_name

    def id(self):
        return self._id


def build_world(per_class=4, seed=70):
    """A profile per class, plus `per_class` features drawn from each."""
    profiles, samples, items = {}, {}, []
    fid = 0
    for i, (name, signature) in enumerate(CLASSES.items()):
        profiles[name] = make_profile(name, signature=signature, n=800,
                                      seed=seed + i)
        for j in range(per_class):
            samples[fid] = sample(signature=signature, n=120, seed=seed + 100 + fid)
            items.append((Feature(fid, name), f"{name}-{j}", name))
            fid += 1
    return profiles, samples, items


class RunBatchTest(unittest.TestCase):

    def setUp(self):
        self.profiles, self.samples, self.items = build_world()
        self.sample_for = lambda f: self.samples[f.id()]

    def profile_for(self, feature):
        name = feature.class_name
        return self.profiles.get(name), "", name

    def test_every_feature_is_assessed_against_its_own_class(self):
        report = batch_mod.run_batch(self.items, self.profile_for, self.sample_for)
        self.assertEqual(report.processed, len(self.items))
        self.assertEqual(report.skipped, [])
        for row in report.rows:
            self.assertEqual(row["etalon"], row["class"])
            self.assertEqual(row["value"], 1)

    def test_a_class_without_a_profile_is_skipped_with_a_reason(self):
        del self.profiles["swamp"]
        report = batch_mod.run_batch(self.items, self.profile_for, self.sample_for)
        self.assertEqual(report.processed, 8)
        self.assertEqual(report.skip_reasons(),
                         {batch_mod.SKIP_NO_PROFILE: 4})

    def test_a_feature_without_pixels_is_skipped(self):
        report = batch_mod.run_batch(
            self.items, self.profile_for,
            lambda f: None if f.id() == 0 else self.samples[f.id()])
        self.assertIn((("pine-0"), "pine", batch_mod.SKIP_NO_PIXELS), report.skipped)

    def test_manual_assessments_can_be_left_alone(self):
        report = batch_mod.run_batch(
            self.items, self.profile_for, self.sample_for,
            skip_key=lambda key: batch_mod.SKIP_MANUAL if key == "pine-1" else None)
        self.assertEqual(report.skip_reasons(), {batch_mod.SKIP_MANUAL: 1})

    def test_a_profile_from_another_raster_is_skipped_not_scored(self):
        self.profiles["pine"] = make_profile(
            "pine", seed=80, bands=["B1", "B2", "B3", "B4"])
        report = batch_mod.run_batch(self.items, self.profile_for, self.sample_for)
        self.assertEqual(report.skip_reasons(), {batch_mod.SKIP_INCOMPATIBLE: 4})

    def test_cancelling_stops_the_run_and_is_recorded(self):
        seen = []
        report = batch_mod.run_batch(
            self.items, self.profile_for, self.sample_for,
            progress=lambda i, total, key: seen.append(key),
            is_cancelled=lambda: len(seen) >= 3)
        self.assertTrue(report.cancelled)
        self.assertLess(report.processed, len(self.items))
        self.assertIn("cancelled", report.summary_text())

    def test_summary_counts_by_class(self):
        report = batch_mod.run_batch(self.items, self.profile_for, self.sample_for)
        counts = report.counts_by_class()
        self.assertEqual(sorted(counts), ["pine", "spruce", "swamp"])
        self.assertEqual(counts["pine"], {1: 4})

    def test_csv_has_one_row_per_feature_and_a_column_per_metric(self):
        report = batch_mod.run_batch(self.items, self.profile_for, self.sample_for)
        path = os.path.join(tempfile.mkdtemp(), "batch.csv")
        report.write_csv(path)
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        self.assertEqual(len(rows), len(self.items))
        self.assertEqual(rows[0]["class"], "pine")
        self.assertEqual(rows[0]["label"], "typical")
        for key in ("sam", "mahalanobis", "inliers"):
            self.assertIn(f"{key}_sim", rows[0])


class SeparabilityTest(unittest.TestCase):

    def setUp(self):
        self.profiles, self.samples, self.items = build_world(per_class=5, seed=90)
        self.sample_for = lambda f: self.samples[f.id()]

    def test_the_matrix_is_diagonally_dominant_for_separable_classes(self):
        report = batch_mod.run_separability(self.items, self.profiles, self.sample_for)
        self.assertEqual(report.processed, len(self.items))
        classes, names, medians, counts = report.matrix()
        for i, class_name in enumerate(classes):
            best = max(range(len(names)), key=lambda j: medians[i][j])
            self.assertEqual(names[best], class_name)
            self.assertEqual(counts[i], 5)

    def test_own_profile_wins_for_every_class(self):
        report = batch_mod.run_separability(self.items, self.profiles, self.sample_for)
        for class_name, share in report.own_etalon_wins().items():
            self.assertEqual(share, 1.0, class_name)

    def test_small_features_can_be_left_out(self):
        report = batch_mod.run_separability(self.items, self.profiles,
                                            self.sample_for, min_pixels=500)
        self.assertEqual(report.processed, 0)
        self.assertEqual({reason for _, _, reason in report.skipped},
                         {batch_mod.SKIP_TOO_SMALL})

    def test_pixels_are_sampled_once_per_feature_not_once_per_profile(self):
        calls = []

        def counting(feature):
            calls.append(feature.id())
            return self.samples[feature.id()]

        batch_mod.run_separability(self.items, self.profiles, counting)
        self.assertEqual(len(calls), len(self.items))

    def test_matrix_csv_columns_are_named_after_the_profiles(self):
        report = batch_mod.run_separability(self.items, self.profiles, self.sample_for)
        path = os.path.join(tempfile.mkdtemp(), "matrix.csv")
        report.write_matrix_csv(path)
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh, delimiter=";"))
        self.assertEqual(rows[0][:3], ["feature_class", "features", "own_profile_best"])
        self.assertEqual(sorted(rows[0][3:]),
                         sorted(f"profile_{name}" for name in self.profiles))


if __name__ == "__main__":
    unittest.main()
