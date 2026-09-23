# -*- coding: utf-8 -*-
"""Sample statistics, covariance regularization and the serializable profile."""
import json
import os
import tempfile
import unittest

import numpy as np

from .support import (BANDS, SIGNATURE, ClassProfile, SpectralStats,
                      FakeProject, make_profile, pixels, profile_mod)


class RegularizeCovTest(unittest.TestCase):

    def test_shrinkage_grows_as_pixels_become_scarce(self):
        cov = np.cov(pixels(n=500, seed=11).values, rowvar=False)
        _, many, _ = profile_mod.regularize_cov(cov, 500)
        _, few, _ = profile_mod.regularize_cov(cov, 8)
        self.assertLess(many, few)
        self.assertAlmostEqual(many, 4 / 500, places=6)

    def test_a_singular_covariance_is_made_invertible(self):
        """A constant band makes the sample covariance singular."""
        values = pixels(seed=12).values.copy()
        values[:, 2] = 1000.0
        stats = SpectralStats(BANDS, values)
        self.assertTrue(np.linalg.matrix_rank(stats.cov) < 4)
        np.linalg.inv(stats.cov_reg)          # must not raise
        self.assertIsNotNone(stats.inv_cov)

    def test_every_band_constant_leaves_no_usable_covariance(self):
        values = np.tile(SIGNATURE, (50, 1))
        stats = SpectralStats(BANDS, values)
        self.assertTrue(stats.singular)

    def test_shrunk_covariance_keeps_the_trace(self):
        cov = np.cov(pixels(n=300, seed=13).values, rowvar=False)
        reg, _, _ = profile_mod.regularize_cov(cov, 300)
        self.assertAlmostEqual(np.trace(reg), np.trace(cov), places=6)


class SpectralStatsTest(unittest.TestCase):

    def test_mean_and_shape(self):
        stats = pixels(n=2000, noise=10.0, seed=14)
        self.assertEqual((stats.n, stats.k), (2000, 4))
        np.testing.assert_allclose(stats.mean, SIGNATURE, atol=2.0)

    def test_band_count_must_match_the_matrix(self):
        with self.assertRaises(ValueError):
            SpectralStats(["Band 1", "Band 2"], np.zeros((10, 4)))

    def test_mahalanobis_of_the_mean_is_zero(self):
        stats = pixels(seed=15)
        d2 = profile_mod.mahalanobis_sq(stats.mean[None, :], stats.mean, stats.inv_cov)
        self.assertAlmostEqual(float(d2[0]), 0.0, places=9)

    def test_percentiles_are_ordered(self):
        for band, values in pixels(seed=16).percentiles().items():
            numbers = [values[key] for key in sorted(values, key=lambda k: int(k[1:]))]
            self.assertEqual(numbers, sorted(numbers), band)


class ClassProfileTest(unittest.TestCase):

    def test_round_trip_through_a_dictionary(self):
        original = make_profile("pine", seed=17)
        restored = ClassProfile.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(restored.name, "pine")
        self.assertEqual(restored.band_names, original.band_names)
        self.assertEqual(restored.n_pixels, original.n_pixels)
        np.testing.assert_allclose(restored.mean, original.mean)
        np.testing.assert_allclose(restored.cov_reg, original.cov_reg)

    def test_round_trip_through_a_file(self):
        original = make_profile("swamp", seed=18)
        path = os.path.join(tempfile.mkdtemp(), "swamp.class.json")
        original.save(path)
        restored = ClassProfile.load(path)
        self.assertEqual(restored.name, "swamp")
        np.testing.assert_allclose(restored.mean, original.mean)

    def test_a_foreign_file_is_refused(self):
        path = os.path.join(tempfile.mkdtemp(), "other.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"format": "something/else"}, fh)
        with self.assertRaises(ValueError):
            ClassProfile.load(path)

    def test_compatibility_checks_band_count_and_names(self):
        profile = make_profile(seed=19)
        self.assertTrue(profile.compatibility(BANDS)[0])
        ok, message = profile.compatibility(["Band 1", "Band 2"])
        self.assertFalse(ok)
        self.assertIn("2", message)
        ok, message = profile.compatibility(["B1", "B2", "B3", "B4"])
        self.assertFalse(ok)
        self.assertIn("band names", message)

    def test_label_is_used_for_display_and_name_for_matching(self):
        profile = ClassProfile.from_stats(pixels(seed=20), name="1200", label="Pine forest")
        self.assertEqual(profile.name, "1200")
        self.assertEqual(profile.title, "Pine forest")
        self.assertEqual(ClassProfile.from_dict(profile.to_dict()).title, "Pine forest")

    def test_storing_in_a_project_and_reading_it_back(self):
        project = FakeProject()
        profile = make_profile("birch", seed=21)
        profile_mod.save_to_project(project, profile)
        restored = profile_mod.load_from_project(project)
        self.assertEqual(restored.name, "birch")
        profile_mod.clear_in_project(project)
        self.assertIsNone(profile_mod.load_from_project(project))


if __name__ == "__main__":
    unittest.main()
