# -*- coding: utf-8 -*-
"""
profile.py
Сводная статистика спектральной выборки и эталонный профиль класса.

SpectralStats  — обёртка над матрицей (n_pixels, n_bands): среднее, ковариация,
                 регуляризация, расстояние Махаланобиса.
ClassProfile   — сериализуемый эталон класса: то же самое плюс гистограммы и
                 перцентили по каналам, привязка к растру и объектам-источникам.

Ковариация вырождается, когда пикселей мало относительно числа каналов
(маленький объект) или канал константный. Поэтому она всегда сжимается к
масштабированной единичной матрице, а коэффициент сжатия попадает в JSON —
чтобы по выгрузке было видно, насколько статистике можно верить.
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
# Ковариация
# ---------------------------------------------------------------------------

def regularize_cov(cov, n_pixels):
    """
    Сжатие ковариации к диагонали: (1-λ)·Σ + λ·(tr Σ / k)·I.

    λ = k/n — чем меньше пикселей на канал, тем сильнее сжатие. При
    необходимости λ увеличивается, пока матрица не станет обратимой.

    Returns (cov_reg, shrinkage, was_singular)
    """
    cov = np.atleast_2d(np.asarray(cov, dtype=np.float64))
    k = cov.shape[0]
    identity = np.eye(k)

    avg_var = float(np.trace(cov)) / k if k else 0.0
    if not np.isfinite(avg_var) or avg_var <= 0:
        # все каналы константны — осмысленной ковариации нет
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
    """d² для каждой строки points относительно (mean, inv_cov)."""
    delta = np.atleast_2d(np.asarray(points, dtype=np.float64)) - np.asarray(mean, dtype=np.float64)
    return np.einsum("ij,jk,ik->i", delta, inv_cov, delta)


# ---------------------------------------------------------------------------
# Статистика выборки
# ---------------------------------------------------------------------------

class SpectralStats:
    """Среднее / ковариация / расстояния для матрицы (n_pixels, n_bands)."""

    def __init__(self, band_names, values):
        self.band_names = list(band_names)
        self.values = np.atleast_2d(np.asarray(values, dtype=np.float64))
        if self.values.shape[1] != len(self.band_names):
            raise ValueError(
                f"каналов {len(self.band_names)}, а столбцов в матрице "
                f"{self.values.shape[1]}")
        self._mean = None
        self._cov = None
        self._cov_reg = None
        self._inv_cov = None
        self._shrinkage = None
        self._singular = None

    @classmethod
    def from_sample(cls, sample):
        """Из RasterSample (raster_utils.extract_rectangle)."""
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
        """Сырая выборочная ковариация (может быть вырожденной)."""
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
        """{band: {counts, edges}} — общие рамки для сравнения распределений."""
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
# Эталон класса
# ---------------------------------------------------------------------------

class ClassProfile:
    """Сериализуемый эталон класса."""

    def __init__(self, name, band_names, n_pixels, mean, cov_reg,
                 shrinkage=0.0, singular=False, percentiles=None, histograms=None,
                 raster=None, source=None, created_at=None, cov_raw=None,
                 label=None):
        #: name — значение поля класса, по нему идёт сопоставление объектов.
        #: label — подпись из легенды слоя, только для показа в интерфейсе.
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
        """Как называть эталон в интерфейсе."""
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
        (ok, сообщение). Эталон и объект должны быть с одинаковым набором
        каналов — иначе сравниваются несопоставимые величины.
        """
        other = list(band_names)
        if other == self.band_names:
            return True, ""
        if len(other) != len(self.band_names):
            return False, (f"каналов у объекта {len(other)}, у эталона "
                           f"{len(self.band_names)} — сравнение невозможно")
        return False, (f"названия каналов не совпадают: {other} vs {self.band_names}. "
                       "Убедитесь, что это тот же растр")

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
            raise ValueError("это не профиль класса")
        if data.get("format", FORMAT) != FORMAT:
            raise ValueError(f"чужой формат профиля: {data.get('format')}")
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
        return (f"<ClassProfile {self.name!r}: {self.n_pixels} пикс., "
                f"{self.k} канал(ов), сжатие {self.shrinkage:.3f}>")


# ---------------------------------------------------------------------------
# Эталон в проекте
# ---------------------------------------------------------------------------

PROJECT_SCOPE = "calc_sp_ch"
PROJECT_ENTRY = "class_profile"


def save_to_project(project, profile):
    """Положить текущий эталон в проект (переживает перезапуск QGIS)."""
    if profile is None:
        return clear_in_project(project)
    project.writeEntry(PROJECT_SCOPE, PROJECT_ENTRY,
                       json.dumps(profile.to_dict(), ensure_ascii=False))
    return True


def load_from_project(project):
    """Достать эталон из проекта или None."""
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
