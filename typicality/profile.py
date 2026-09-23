# -*- coding: utf-8 -*-
"""
profile.py
Summary statistics of a spectral sample, and the reference profile of a class.

SpectralStats  - a wrapper around an (n_pixels, n_bands) matrix: mean,
                 covariance, regularization, Mahalanobis distance.
ClassProfile   - a serializable class profile: the same, plus per-band
                 histograms and percentiles, and links to the source raster and
                 features.

Covariance degenerates when there are few pixels relative to the number of bands
(a small feature) or when a band is constant. It is therefore always shrunk
towards a scaled identity matrix, and the shrinkage coefficient goes into the
JSON, so the export shows how far the statistics can be trusted.
"""

import json
from datetime import datetime

import numpy as np

DEFAULT_BINS = 64
FORMAT = "calc_sp_ch/class-profile"
VERSION = 1


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Covariance
# ---------------------------------------------------------------------------

def regularize_cov(cov, n_pixels):
    """
    Shrink the covariance towards the diagonal: (1-l)*S + l*(tr S / k)*I.

    l = k/n - the fewer pixels per band, the stronger the shrinkage. If need be,
    l is raised further until the matrix becomes invertible.

    Returns (cov_reg, shrinkage, was_singular)
    """
    cov = np.atleast_2d(np.asarray(cov, dtype=np.float64))
    k = cov.shape[0]
    identity = np.eye(k)

    avg_var = float(np.trace(cov)) / k if k else 0.0
    if not np.isfinite(avg_var) or avg_var <= 0:
        # every band is constant, there is no meaningful covariance
        return identity * 1e-12, 1.0, True

    lam = float(np.clip(k / max(int(n_pixels), 1), 0.0, 1.0))
    singular = False
    for _ in range(12):
        reg = (1.0 - lam) * cov + lam * avg_var * identity
        try:
            np.linalg.cholesky(reg)
            return reg, lam, singular
        except np.linalg.LinAlgError:
            singular = True
            lam = min(1.0, lam * 2 + 1e-3)
    return avg_var * identity, 1.0, True


def mahalanobis_sq(points, mean, inv_cov):
    """d^2 for every row of points with respect to (mean, inv_cov)."""
    delta = np.atleast_2d(np.asarray(points, dtype=np.float64)) - np.asarray(mean, dtype=np.float64)
    return np.einsum("ij,jk,ik->i", delta, inv_cov, delta)


# ---------------------------------------------------------------------------
# Sample statistics
# ---------------------------------------------------------------------------

class SpectralStats:
    """Mean / covariance / distances for an (n_pixels, n_bands) matrix."""

    def __init__(self, band_names, values):
        self.band_names = list(band_names)
        self.values = np.atleast_2d(np.asarray(values, dtype=np.float64))
        if self.values.shape[1] != len(self.band_names):
            raise ValueError(
                f"bands: {len(self.band_names)}, but the matrix has columns: "
                f"{self.values.shape[1]}")
        self._mean = None
        self._cov = None
        self._cov_reg = None
        self._inv_cov = None
        self._shrinkage = None
        self._singular = None

    @classmethod
    def from_sample(cls, sample):
        """Build from a RasterSample (raster_utils.extract_rectangle)."""
        return cls(sample.band_names, sample.values)

    @property
    def n(self):
        return int(self.values.shape[0])

    @property
    def k(self):
        return int(self.values.shape[1])

    @property
    def mean(self):
        if self._mean is None:
            self._mean = self.values.mean(axis=0)
        return self._mean

    @property
    def cov(self):
        """Raw sample covariance (may be singular)."""
        if self._cov is None:
            if self.n < 2:
                self._cov = np.zeros((self.k, self.k))
            else:
                self._cov = np.atleast_2d(np.cov(self.values, rowvar=False))
        return self._cov

    def _ensure_reg(self):
        if self._cov_reg is None:
            self._cov_reg, self._shrinkage, self._singular = regularize_cov(self.cov, self.n)
            self._inv_cov = np.linalg.inv(self._cov_reg)

    @property
    def cov_reg(self):
        self._ensure_reg()
        return self._cov_reg

    @property
    def inv_cov(self):
        self._ensure_reg()
        return self._inv_cov

    @property
    def shrinkage(self):
        self._ensure_reg()
        return self._shrinkage

    @property
    def singular(self):
        self._ensure_reg()
        return self._singular

    def percentiles(self, qs=(5, 25, 50, 75, 95)):
        result = {}
        for i, name in enumerate(self.band_names):
            values = np.percentile(self.values[:, i], qs)
            result[name] = {f"p{q}": float(v) for q, v in zip(qs, values)}
        return result

    def histograms(self, bins=DEFAULT_BINS):
        """{band: {counts, edges}} - shared bounds for comparing distributions."""
        result = {}
        for i, name in enumerate(self.band_names):
            column = self.values[:, i]
            lo, hi = float(column.min()), float(column.max())
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                hi = lo + 1.0
            counts, edges = np.histogram(column, bins=bins, range=(lo, hi))
            result[name] = {"counts": counts.tolist(), "edges": edges.tolist()}
        return result


# ---------------------------------------------------------------------------
# Class profile
# ---------------------------------------------------------------------------

class ClassProfile:
    """A serializable class profile."""

    def __init__(self, name, band_names, n_pixels, mean, cov_reg,
                 shrinkage=0.0, singular=False, percentiles=None, histograms=None,
                 raster=None, source=None, created_at=None, cov_raw=None,
                 label=None):
        #: name - the class field value; features are matched against it.
        #: label - the legend label, used only for display in the interface.
        self.name = name
        self.label = label or None
        self.band_names = list(band_names)
        self.n_pixels = int(n_pixels)
        self.mean = np.asarray(mean, dtype=np.float64)
        self.cov_reg = np.atleast_2d(np.asarray(cov_reg, dtype=np.float64))
        self.cov_raw = None if cov_raw is None else np.atleast_2d(np.asarray(cov_raw, np.float64))
        self.shrinkage = float(shrinkage)
        self.singular = bool(singular)
        self.percentiles = percentiles or {}
        self.histograms = histograms or {}
        self.raster = raster or {}
        self.source = source or {}
        self.created_at = created_at or _now()
        self._inv_cov = None

    # ---------------------------------------------------------------- build

    @property
    def title(self):
        """How to name the profile in the interface."""
        return self.label or self.name

    @classmethod
    def from_stats(cls, stats, name="class", raster=None, source=None,
                   bins=DEFAULT_BINS, label=None):
        return cls(
            name=name,
            label=label,
            band_names=stats.band_names,
            n_pixels=stats.n,
            mean=stats.mean,
            cov_reg=stats.cov_reg,
            cov_raw=stats.cov,
            shrinkage=stats.shrinkage,
            singular=stats.singular,
            percentiles=stats.percentiles(),
            histograms=stats.histograms(bins),
            raster=raster,
            source=source,
        )

    @classmethod
    def from_sample(cls, sample, name="class", raster=None, source=None,
                    bins=DEFAULT_BINS, label=None):
        return cls.from_stats(SpectralStats.from_sample(sample), name, raster, source,
                              bins, label)

    # ----------------------------------------------------------- properties

    @property
    def k(self):
        return len(self.band_names)

    @property
    def inv_cov(self):
        if self._inv_cov is None:
            try:
                self._inv_cov = np.linalg.inv(self.cov_reg)
            except np.linalg.LinAlgError:
                self._inv_cov = np.linalg.pinv(self.cov_reg)
        return self._inv_cov

    def mahalanobis_sq(self, points):
        return mahalanobis_sq(points, self.mean, self.inv_cov)

    def compatibility(self, band_names):
        """
        (ok, message). The profile and the feature must have the same set of
        bands, otherwise incomparable quantities would be compared.
        """
        other = list(band_names)
        if other == self.band_names:
            return True, ""
        if len(other) != len(self.band_names):
            return False, (f"bands in the feature: {len(other)}, in the profile: "
                           f"{len(self.band_names)} - comparison is impossible")
        return False, (f"band names do not match: {other} vs {self.band_names}. "
                       "Make sure this is the same raster")

    # -------------------------------------------------------- (de)serialize

    def to_dict(self):
        return {
            "format": FORMAT,
            "version": VERSION,
            "name": self.name,
            "label": self.label,
            "created_at": self.created_at,
            "band_names": self.band_names,
            "n_pixels": self.n_pixels,
            "mean": self.mean.tolist(),
            "cov": self.cov_reg.tolist(),
            "cov_raw": None if self.cov_raw is None else self.cov_raw.tolist(),
            "shrinkage": self.shrinkage,
            "singular": self.singular,
            "percentiles": self.percentiles,
            "histograms": self.histograms,
            "raster": self.raster,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or "band_names" not in data:
            raise ValueError("this is not a class profile")
        if data.get("format", FORMAT) != FORMAT:
            raise ValueError(f"unknown profile format: {data.get('format')}")
        return cls(
            name=data.get("name", "class"),
            label=data.get("label"),
            band_names=data["band_names"],
            n_pixels=data.get("n_pixels", 0),
            mean=data["mean"],
            cov_reg=data["cov"],
            cov_raw=data.get("cov_raw"),
            shrinkage=data.get("shrinkage", 0.0),
            singular=data.get("singular", False),
            percentiles=data.get("percentiles"),
            histograms=data.get("histograms"),
            raster=data.get("raster"),
            source=data.get("source"),
            created_at=data.get("created_at"),
        )

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def __repr__(self):
        return (f"<ClassProfile {self.name!r}: {self.n_pixels} px, "
                f"{self.k} band(s), shrinkage {self.shrinkage:.3f}>")


# ---------------------------------------------------------------------------
# Class profile stored in the project
# ---------------------------------------------------------------------------

PROJECT_SCOPE = "calc_sp_ch"
PROJECT_ENTRY = "class_profile"


def save_to_project(project, profile):
    """Store the current profile in the project (survives a QGIS restart)."""
    if profile is None:
        return clear_in_project(project)
    project.writeEntry(PROJECT_SCOPE, PROJECT_ENTRY,
                       json.dumps(profile.to_dict(), ensure_ascii=False))
    return True


def load_from_project(project):
    """Read the profile back from the project, or None."""
    raw, ok = project.readEntry(PROJECT_SCOPE, PROJECT_ENTRY)
    if not ok or not raw:
        return None
    try:
        return ClassProfile.from_dict(json.loads(raw))
    except (ValueError, TypeError, KeyError):
        return None


def clear_in_project(project):
    project.removeEntry(PROJECT_SCOPE, PROJECT_ENTRY)
    return True
