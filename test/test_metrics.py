# -*- coding: utf-8 -*-
"""Similarity metrics and their aggregation into a typicality score."""
import math
import unittest

import numpy as np

from .support import SIGNATURE, SpectralStats, make_profile, metrics, pixels


class Chi2Test(unittest.TestCase):
    """The pure-Python chi-square fallback, used when scipy is absent."""

    def test_survival_function_against_known_values(self):
        # chi2(k=3): P(X > 7.815) = 0.05; chi2(k=4): P(X > 9.488) = 0.05
        self.assertAlmostEqual(metrics.chi2_sf(7.8147, 3), 0.05, places=4)
        self.assertAlmostEqual(metrics.chi2_sf(9.4877, 4), 0.05, places=4)
        self.assertAlmostEqual(metrics.chi2_sf(0.0, 5), 1.0, places=6)

    def test_inverse_is_consistent(self):
        for df in (1, 3, 4, 10):
            x = metrics.chi2_isf(0.05, df)
            self.assertAlmostEqual(metrics.chi2_sf(x, df), 0.05, places=6)

    def test_ninety_five_percent_quantiles(self):
        self.assertAlmostEqual(metrics.chi2_isf(0.05, 3), 7.8147, places=3)
        self.assertAlmostEqual(metrics.chi2_isf(0.05, 4), 9.4877, places=3)


class MetricsTest(unittest.TestCase):

    def setUp(self):
        self.profile = make_profile(seed=1)
        self.same = pixels(seed=2)

    def test_a_feature_drawn_from_the_class_scores_as_typical(self):
        result = metrics.evaluate(self.profile, self.same)
        self.assertGreater(result.score, 0.9)
        self.assertEqual(result.value, 1)

    def test_spectral_angle_ignores_brightness(self):
        """A shadow scales the whole spectrum, so the angle must stay ~0."""
        scaled = pixels(SIGNATURE * 0.6, seed=3, noise=20.0)
        angle = metrics.spectral_angle(self.profile, scaled)
        self.assertLess(angle.raw, 0.01)
        self.assertGreater(angle.similarity, 0.9)

    def test_mahalanobis_does_notice_brightness(self):
        scaled = pixels(SIGNATURE * 0.6, seed=3, noise=20.0)
        maha = metrics.mahalanobis_mean(self.profile, scaled)
        self.assertLess(maha.similarity, 1e-6)

    def test_score_falls_as_the_feature_moves_away(self):
        sigma = 40.0
        scores = []
        for offset in (0.0, 1.0, 2.0, 4.0, 8.0):
            shifted = SIGNATURE + np.array([1.0, -1.0, 1.0, -1.0]) * offset * sigma
            scores.append(metrics.evaluate(self.profile, pixels(shifted, seed=4)).score)
        for earlier, later in zip(scores, scores[1:]):
            self.assertGreater(earlier, later)
        self.assertEqual(metrics.classify(scores[0]), 1)
        self.assertEqual(metrics.classify(scores[-1]), 3)

    def test_inlier_fraction_matches_the_confidence_level(self):
        """With alpha=0.05 about 95% of the class's own pixels must fall inside."""
        result = metrics.inlier_fraction(self.profile, self.same)
        self.assertAlmostEqual(result.similarity, 0.95, delta=0.04)

    def test_per_pixel_sam_sees_a_mixture_that_the_mean_hides(self):
        """
        Two halves of differing spectral SHAPE average out to the profile mean.

        The offset is deliberately not parallel to the signature: a parallel one
        would only change brightness, which SAM is designed to ignore.
        """
        offset = np.array([600.0, -500.0, 500.0, -900.0])
        mixed = np.vstack([
            np.random.default_rng(5).normal(SIGNATURE + offset, 20, (300, 4)),
            np.random.default_rng(6).normal(SIGNATURE - offset, 20, (300, 4)),
        ])
        candidate = SpectralStats(self.profile.band_names, mixed)
        by_mean = metrics.spectral_angle(self.profile, candidate)
        by_pixel = metrics.spectral_angle_pixels(self.profile, candidate)
        self.assertGreater(by_mean.similarity, 0.9)
        self.assertLess(by_pixel.similarity, 0.1)

    def test_classify_uses_the_thresholds(self):
        self.assertEqual(metrics.classify(0.80), 1)
        self.assertEqual(metrics.classify(0.66), 1)
        self.assertEqual(metrics.classify(0.50), 2)
        self.assertEqual(metrics.classify(0.10), 3)
        self.assertIsNone(metrics.classify(None))

    def test_classify_honours_custom_thresholds(self):
        self.assertEqual(metrics.classify(0.50, (0.4, 0.2)), 1)
        self.assertEqual(metrics.classify(0.50, (0.9, 0.6)), 3)


class AggregationTest(unittest.TestCase):

    def setUp(self):
        self.profile = make_profile(seed=7)
        self.results, self.notes, _ = metrics.run_metrics(self.profile, pixels(seed=8))

    def test_only_weighted_metrics_are_applied(self):
        result = metrics.aggregate(self.results, weights={"sam": 1.0})
        self.assertEqual(set(result.weights), {"sam"})
        self.assertAlmostEqual(result.score, self.results["sam"].similarity, places=9)

    def test_weight_of_an_excluded_metric_is_redistributed(self):
        broken = dict(self.results)
        broken["sam"] = metrics.MetricResult("sam", "SAM", ok=False, note="no data")
        result = metrics.aggregate(broken, weights={"sam": 1.0, "inliers": 1.0})
        self.assertEqual(set(result.weights), {"inliers"})
        self.assertAlmostEqual(result.score, broken["inliers"].similarity, places=9)
        self.assertTrue(any("excluded" in note for note in result.notes))

    def test_no_usable_metric_means_no_assessment(self):
        dead = {key: metrics.MetricResult(key, key, ok=False, note="no data")
                for key in self.results}
        result = metrics.aggregate(dead)
        self.assertIsNone(result.score)
        self.assertIsNone(result.value)

    def test_product_mode_is_stricter_than_sum(self):
        weights = {"sam": 1.0, "inliers": 1.0}
        results = dict(self.results)
        results["inliers"] = metrics.MetricResult("inliers", "Inliers", raw=0.0,
                                                  similarity=0.02, ok=True)
        results["sam"] = metrics.MetricResult("sam", "SAM", raw=0.0,
                                              similarity=1.0, ok=True)
        by_sum = metrics.aggregate(results, weights, mode=metrics.MODE_SUM).score
        by_product = metrics.aggregate(results, weights, mode=metrics.MODE_PRODUCT).score
        self.assertAlmostEqual(by_sum, 0.51, places=6)
        self.assertAlmostEqual(by_product, math.sqrt(0.02), places=6)
        self.assertLess(by_product, by_sum)

    def test_default_weights_balance_mean_based_and_pixel_based_evidence(self):
        mean_based = sum(metrics.DEFAULT_WEIGHTS[k] for k in metrics.MEAN_BASED)
        pixel_based = sum(metrics.DEFAULT_WEIGHTS[k] for k in metrics.PIXEL_BASED)
        self.assertAlmostEqual(mean_based, pixel_based, places=9)

    def test_notes_survive_into_the_result(self):
        result = metrics.aggregate(self.results, notes=["hello"])
        self.assertIn("hello", result.notes)


class IncompatibleProfileTest(unittest.TestCase):

    def test_band_count_mismatch_is_refused(self):
        profile = make_profile(seed=9)
        other = pixels(SIGNATURE[:3], seed=10, bands=["Band 1", "Band 2", "Band 3"])
        with self.assertRaises(ValueError):
            metrics.run_metrics(profile, other)


if __name__ == "__main__":
    unittest.main()
