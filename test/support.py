# -*- coding: utf-8 -*-
"""
Shared helpers for the tests.

The modules under test are deliberately free of QGIS imports, so the suite runs
under a plain Python with numpy. Importing the plugin as a package would pull in
qgis through calc_sp_ch/__init__.py, so the package directory is registered
under its own name instead.
"""
import importlib
import importlib.machinery
import os
import sys
import types

import numpy as np

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _root_package(name, path):
    """
    Register `path` as a package named `name` without executing its __init__.

    The plugin's own __init__.py is the QGIS entry point; the subpackages
    underneath are then imported normally, so their __init__ files do run.
    """
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [path]
        pkg.__spec__ = importlib.machinery.ModuleSpec(
            name, loader=None, is_package=True)
        pkg.__spec__.submodule_search_locations = [path]
        sys.modules[name] = pkg
    return sys.modules[name]


_root_package("cspch", PLUGIN_DIR)

typicality = importlib.import_module("cspch.typicality")
metrics = importlib.import_module("cspch.typicality.metrics")
profile_mod = importlib.import_module("cspch.typicality.profile")
library_mod = importlib.import_module("cspch.typicality.library")
batch_mod = importlib.import_module("cspch.typicality.batch")
histogram_mod = importlib.import_module("cspch.raster_utils.histogram")

SpectralStats = profile_mod.SpectralStats
ClassProfile = profile_mod.ClassProfile

BANDS = ["Band 1", "Band 2", "Band 3", "Band 4"]
SIGNATURE = np.array([900.0, 1400.0, 1100.0, 3000.0])


def pixels(signature=SIGNATURE, n=600, noise=40.0, seed=0, bands=BANDS):
    """A cloud of pixels around `signature`, shaped (n, len(bands))."""
    rng = np.random.default_rng(seed)
    values = rng.normal(signature, noise, (n, len(signature)))
    return SpectralStats(bands, values)


def make_profile(name="forest", label=None, **kwargs):
    return ClassProfile.from_stats(pixels(**kwargs), name=name, label=label)


class FakeSample:
    """The slice of raster_utils.RasterSample that the typicality code reads."""

    def __init__(self, stats):
        self.band_names = list(stats.band_names)
        self.values = stats.values
        self.n_pixels = stats.n
        self.total_pixels = stats.n
        self.subsampled = False

    def __bool__(self):
        return self.n_pixels > 0


def sample(**kwargs):
    """Pixels wrapped the way the raster sampler would hand them over."""
    return FakeSample(pixels(**kwargs))


class FakeProject:
    """The slice of QgsProject that ProfileLibrary and friends actually use."""

    def __init__(self):
        self.entries = {}
        self.dirty = False

    def readEntry(self, scope, key, default=""):
        value = self.entries.get((scope, key))
        return (value, True) if value is not None else (default, False)

    def writeEntry(self, scope, key, value):
        self.entries[(scope, key)] = value
        return True

    def removeEntry(self, scope, key):
        self.entries.pop((scope, key), None)
        return True

    def setDirty(self, state=True):
        self.dirty = state
