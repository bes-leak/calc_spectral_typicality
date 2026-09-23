# -*- coding: utf-8 -*-
"""Adaptive binning and the typicality scale."""
import unittest

import numpy as np

from .support import histogram_mod, typicality

histogram = histogram_mod.histogram


class HistogramTest(unittest.TestCase):

    def test_bin_count_stays_within_bounds(self):
        rng = np.random.default_rng(0)
        for values in (rng.normal(1000, 50, 5000),
                       rng.normal(1000, 1, 50),
                       rng.integers(0, 65535, 20000).astype(float)):
            counts, edges = histogram(values)
            self.assertGreaterEqual(len(counts), histogram_mod.MIN_BINS)
            self.assertLessEqual(len(counts), histogram_mod.MAX_BINS)
            self.assertEqual(len(edges), len(counts) + 1)

    def test_a_heavy_tail_does_not_explode_the_bin_count(self):
        """A narrow IQR with a wide range is where Freedman-Diaconis runs away."""
        values = np.concatenate([np.full(10000, 1000.0) + np.arange(10000) * 1e-3,
                                 np.array([65535.0])])
        counts, _ = histogram(values)
        self.assertLessEqual(len(counts), histogram_mod.MAX_BINS)

    def test_identical_values_still_give_a_histogram(self):
        counts, edges = histogram(np.full(500, 42.0))
        self.assertEqual(int(np.sum(counts)), 500)
        self.assertGreater(edges[-1], edges[0])

    def test_every_pixel_lands_in_some_bin(self):
        values = np.random.default_rng(1).normal(500, 120, 4000)
        counts, _ = histogram(values)
        self.assertEqual(int(np.sum(counts)), values.size)

    def test_an_empty_array_is_handled(self):
        counts, edges = histogram(np.array([]))
        self.assertEqual(int(np.sum(counts)), 0)
        self.assertEqual(len(edges), len(counts) + 1)


class TypicalityScaleTest(unittest.TestCase):

    def test_the_scale_runs_from_typical_to_atypical(self):
        self.assertEqual(typicality.VALUES, (1, 2, 3))
        self.assertEqual(typicality.label_for(1), "typical")
        self.assertEqual(typicality.label_for(2), "doubtful")
        self.assertEqual(typicality.label_for(3), "atypical")
        self.assertEqual(typicality.label_for(None), "not set")

    def test_normalize_accepts_strings_and_rejects_the_rest(self):
        self.assertEqual(typicality.normalize("2"), 2)
        self.assertEqual(typicality.normalize(3), 3)
        for junk in (0, 7, -1, "x", "", None, [1]):
            self.assertIsNone(typicality.normalize(junk), junk)

    def test_is_valid(self):
        self.assertTrue(typicality.is_valid(None))
        self.assertTrue(all(typicality.is_valid(v) for v in typicality.VALUES))
        self.assertFalse(typicality.is_valid(0))


if __name__ == "__main__":
    unittest.main()
