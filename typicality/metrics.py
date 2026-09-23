# -*- coding: utf-8 -*-
"""
metrics.py
Metrics of how closely a feature matches a class profile, and their aggregation
into a single score.

Every metric returns a raw value (in its own units) and a similarity in [0, 1],
where 1 means a match with the profile. The result is
score = sum(w_i * sim_i) / sum(w_i) over the enabled metrics that could be
computed. A metric that lacked the data it needs (a singular covariance on a
tiny feature, say) is excluded with a note and its weight is redistributed -
handing out silent garbage is not an option.

A note on Mahalanobis: the distance is measured against the SPREAD of the class
(Sigma), not against the standard error of the mean (Sigma/n). Otherwise, with
many pixels, any difference of the means however negligible would give p~0 and
every feature would come out atypical. The question asked here is a different
one: "is it plausible that this feature is one more realization of the class?"
"""

import math

import numpy as np

from .profile import SpectralStats, regularize_cov

try:                                    # scipy ships with QGIS, but not in every build
    from scipy.stats import chi2 as _chi2
except Exception:                       # pragma: no cover
    _chi2 = None


# ---------------------------------------------------------------------------
# chi-square without requiring scipy
# ---------------------------------------------------------------------------

def _gser(a, x, itmax=500, eps=3e-14):
    ap, total, delta = a, 1.0 / a, 1.0 / a
    for _ in range(itmax):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * eps:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a, x, itmax=500, eps=3e-14):
    tiny = 1e-300
    b, c, d = x + 1.0 - a, 1.0 / tiny, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, itmax + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def chi2_sf(x, df):
    """P(X > x) for chi-square with df degrees of freedom."""
    if _chi2 is not None:
        return float(_chi2.sf(x, df))
    a, x = df / 2.0, x / 2.0
    if x <= 0:
        return 1.0
    return 1.0 - _gser(a, x) if x < a + 1.0 else _gcf(a, x)


def chi2_isf(p, df):
    """Quantile: the x for which P(X > x) = p."""
    if _chi2 is not None:
        return float(_chi2.isf(p, df))
    lo, hi = 0.0, max(df * 4.0, 10.0)
    while chi2_sf(hi, df) > p:
        hi *= 2.0
        if hi > 1e12:
            return hi
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if chi2_sf(mid, df) > p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

class MetricResult:
    __slots__ = ("key", "name", "raw", "unit", "similarity", "ok", "note")

    def __init__(self, key, name, raw=None, unit="", similarity=None, ok=True, note=""):
        self.key = key
        self.name = name
        self.raw = raw
        self.unit = unit
        self.similarity = similarity
        self.ok = bool(ok) and similarity is not None
        self.note = note

    def to_dict(self, weight=None):
        data = {
            "name": self.name,
            "raw": None if self.raw is None else float(self.raw),
            "unit": self.unit,
            "similarity": None if self.similarity is None else float(self.similarity),
            "ok": self.ok,
        }
        if self.note:
            data["note"] = self.note
        if weight is not None:
            data["weight"] = float(weight)
        return data

    def __repr__(self):
        state = f"{self.similarity:.3f}" if self.ok else f"— ({self.note})"
        return f"<{self.key}: raw={self.raw} sim={state}>"


class TypicalityResult:
    """The outcome of an automatic assessment."""

    def __init__(self, score, value, metrics, weights, thresholds, notes=None,
                 mode="sum"):
        self.score = score
        self.value = value
        self.metrics = metrics                # {key: MetricResult}
        self.weights = weights                # {key: weight actually applied}
        self.thresholds = thresholds
        self.notes = notes or []
        self.mode = mode

    def contributions(self):
        """{key: the metric's contribution to the score}, used to explain the verdict."""
        total = sum(self.weights.values())
        if not total:
            return {}
        return {key: weight / total * self.metrics[key].similarity
                for key, weight in self.weights.items()
                if self.metrics.get(key) and self.metrics[key].ok}

    def to_dict(self):
        return {
            "score": None if self.score is None else round(float(self.score), 6),
            "value": self.value,
            "mode": self.mode,
            "thresholds": list(self.thresholds),
            "metrics": {k: m.to_dict(self.weights.get(k)) for k, m in self.metrics.items()},
            "notes": self.notes,
        }

    def __repr__(self):
        score = "—" if self.score is None else f"{self.score:.3f}"
        return f"<Typicality score={score} value={self.value}>"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

#: Scale for turning a spectral angle into a similarity, in radians.
#: A linear scale 1 - a/(pi/2) will not do: real angles between classes fall in
#: 0.02-0.3 rad, so the similarity would always come out above 0.98 and SAM would
#: distinguish nothing. exp(-a/a0) gives 0.5 at a ~ 0.10 rad, the usual "same
#: class" threshold in remote sensing.
SAM_SCALE = 0.15


def spectral_angle(profile, cand, scale=SAM_SCALE):
    """
    SAM: the angle between the mean vectors.

    Invariant to brightness: if the feature spectrum differs from the profile by
    a multiplier only (shadow, illumination), the angle is zero and SAM returns
    1. That is exactly why SAM is paired with Mahalanobis: the first catches the
    shape of the spectrum, the second its absolute levels.
    """
    a = np.asarray(profile.mean, dtype=np.float64)
    b = np.asarray(cand.mean, dtype=np.float64)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 0:
        return MetricResult("sam", "Spectral angle (SAM)", ok=False,
                            note="the mean vector is zero")
    cos = float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))
    angle = math.acos(cos)
    similarity = float(np.clip(math.exp(-angle / max(scale, 1e-9)), 0.0, 1.0))
    return MetricResult("sam", "Spectral angle (SAM)", raw=angle, unit="rad",
                        similarity=similarity)


#: Threshold for the per-pixel spectral angle, in radians.
#: Noise in individual pixels inflates the angle by an order of magnitude
#: compared with the angle on mean spectra (on a homogeneous feature the median
#: is ~0.02 rad against 0.0007), so this threshold is its own and much softer.
SAM_PIXEL_THRESHOLD = 0.10


def spectral_angle_pixels(profile, cand, threshold=SAM_PIXEL_THRESHOLD):
    """
    Per-pixel SAM: the share of the feature's pixels whose spectrum is close to
    the profile.

    SAM on mean spectra is blind to heterogeneity: on a feature made of two
    dissimilar spectra the mean may coincide with the profile by chance and the
    angle comes out zero, although no single pixel resembles the profile. This
    metric looks at every pixel and, unlike the inlier fraction, does not depend
    on brightness - it captures the shape of the spectrum.
    """
    reference = np.asarray(profile.mean, dtype=np.float64)
    ref_norm = float(np.linalg.norm(reference))
    if ref_norm <= 0:
        return MetricResult("sam_pixels", "Per-pixel SAM (fraction)", ok=False,
                            note="the profile mean vector is zero")

    values = cand.values
    norms = np.linalg.norm(values, axis=1)
    valid = norms > 0
    if not np.any(valid):
        return MetricResult("sam_pixels", "Per-pixel SAM (fraction)", ok=False,
                            note="no pixel has a non-zero spectrum")

    cos = np.clip(values[valid] @ reference / (norms[valid] * ref_norm), -1.0, 1.0)
    angles = np.arccos(cos)
    fraction = float(np.count_nonzero(angles <= threshold) / angles.size)
    median = float(np.median(angles))
    note = f"median {median:.3f} rad, threshold {threshold:g}"
    if not np.all(valid):
        note += f", pixels skipped: {int(np.count_nonzero(~valid))}"
    return MetricResult("sam_pixels", "Per-pixel SAM (fraction)", raw=median, unit="rad",
                        similarity=fraction, note=note)


def mahalanobis_mean(profile, cand):
    """Mahalanobis distance from the feature mean to the profile -> chi-square p-value."""
    if profile.singular:
        return MetricResult("mahalanobis", "Mahalanobis (p-value)", ok=False,
                            note="the profile covariance is singular")
    d2 = float(profile.mahalanobis_sq([cand.mean])[0])
    if not np.isfinite(d2):
        return MetricResult("mahalanobis", "Mahalanobis (p-value)", ok=False,
                            note="the distance is undefined")
    p = float(chi2_sf(d2, profile.k))
    return MetricResult("mahalanobis", "Mahalanobis (p-value)", raw=d2, unit="d²",
                        similarity=float(np.clip(p, 0.0, 1.0)))


def inlier_fraction(profile, cand, alpha=0.05):
    """Share of the feature's pixels inside the profile's 1-alpha ellipsoid."""
    if profile.singular:
        return MetricResult("inliers", "Inlier fraction", ok=False,
                            note="the profile covariance is singular")
    threshold = chi2_isf(alpha, profile.k)
    d2 = profile.mahalanobis_sq(cand.values)
    d2 = d2[np.isfinite(d2)]
    if d2.size == 0:
        return MetricResult("inliers", "Inlier fraction", ok=False,
                            note="no valid pixels")
    fraction = float(np.count_nonzero(d2 <= threshold) / d2.size)
    # the raw value shows the median d^2; otherwise the column would just repeat
    # the similarity as the very same fraction
    return MetricResult("inliers", "Inlier fraction", raw=float(np.median(d2)),
                        unit="d²", similarity=fraction,
                        note=f"fraction {fraction:.3f} at threshold d^2 = {threshold:.2f} (α={alpha})")


def jeffries_matusita(profile, cand):
    """
    Jeffries-Matusita distance between two Gaussians, via the Bhattacharyya
    distance. JM is in [0, 2]: 0 - the distributions coincide, 2 - they are
    fully separable.
    """
    k = profile.k
    if cand.n <= k:
        return MetricResult("jm", "Jeffries–Matusita", ok=False,
                            note=f"pixels {cand.n} <= bands {k}: the feature covariance is undefined")
    cov1 = np.asarray(profile.cov_reg, dtype=np.float64)
    cov2, _, _ = regularize_cov(cand.cov, cand.n)
    mean_cov = 0.5 * (cov1 + cov2)

    delta = np.asarray(cand.mean) - np.asarray(profile.mean)
    try:
        term1 = 0.125 * float(delta @ np.linalg.inv(mean_cov) @ delta)
    except np.linalg.LinAlgError:
        return MetricResult("jm", "Jeffries–Matusita", ok=False,
                            note="the averaged covariance is singular")

    sign_m, logdet_m = np.linalg.slogdet(mean_cov)
    sign_1, logdet_1 = np.linalg.slogdet(cov1)
    sign_2, logdet_2 = np.linalg.slogdet(cov2)
    if min(sign_m, sign_1, sign_2) <= 0:
        return MetricResult("jm", "Jeffries–Matusita", ok=False,
                            note="the covariance is not positive definite")

    term2 = 0.5 * (logdet_m - 0.5 * (logdet_1 + logdet_2))
    bhattacharyya = max(term1 + term2, 0.0)
    jm = 2.0 * (1.0 - math.exp(-bhattacharyya))
    return MetricResult("jm", "Jeffries–Matusita", raw=jm, unit="JM",
                        similarity=float(np.clip(1.0 - jm / 2.0, 0.0, 1.0)))


def histogram_intersection(profile, cand):
    """
    Intersection of per-band histograms ON THE PROFILE'S BINS.

    Shares are taken over the feature's full pixel count, so pixels that fall
    outside the profile range honestly reduce the similarity instead of dropping
    out of the calculation.
    """
    if not profile.histograms:
        return MetricResult("hist", "Histogram intersection", ok=False,
                            note="the profile carries no histograms")
    scores = []
    for i, band in enumerate(profile.band_names):
        spec = profile.histograms.get(band)
        if not spec:
            continue
        edges = np.asarray(spec["edges"], dtype=np.float64)
        ref = np.asarray(spec["counts"], dtype=np.float64)
        ref_total = ref.sum()
        if ref_total <= 0 or edges.size < 2:
            continue
        counts, _ = np.histogram(cand.values[:, i], bins=edges)
        scores.append(float(np.minimum(ref / ref_total, counts / max(cand.n, 1)).sum()))
    if not scores:
        return MetricResult("hist", "Histogram intersection", ok=False,
                            note="the bands could not be matched up")
    value = float(np.mean(scores))
    return MetricResult("hist", "Histogram intersection", raw=value, unit="",
                        similarity=value, note=f"averaged over {len(scores)} band(s)")


# ---------------------------------------------------------------------------
# Registry and aggregation
# ---------------------------------------------------------------------------

METRICS = {
    "sam": spectral_angle,
    "mahalanobis": mahalanobis_mean,
    "sam_pixels": spectral_angle_pixels,
    "inliers": inlier_fraction,
    "jm": jeffries_matusita,
    "hist": histogram_intersection,
}

METRIC_ORDER = ("sam", "mahalanobis", "sam_pixels", "inliers", "jm", "hist")

#: Mean-based metrics look at the feature signature as a whole, pixel-based ones
#: at every pixel. These are different kinds of evidence, so weight is split
#: between the groups rather than between the metrics: otherwise two correlated
#: mean-based metrics outvote the pixel-based ones and a heterogeneous feature
MEAN_BASED = ("sam", "mahalanobis", "jm", "hist")
PIXEL_BASED = ("sam_pixels", "inliers")

METRIC_GROUPS = {key: ("mean-based" if key in MEAN_BASED else "pixel-based")
                 for key in METRIC_ORDER}

DEFAULT_WEIGHTS = {
    "sam": 0.25,            # -. mean-based - 0.5
    "mahalanobis": 0.25,    # ┘
    "sam_pixels": 0.25,     # -. pixel-based - 0.5
    "inliers": 0.25,        # ┘
    "jm": 0.0,
    "hist": 0.0,
}

# score >= 0.66 -> 1 (typical); >= 0.33 -> 2; otherwise 3
DEFAULT_THRESHOLDS = (0.66, 0.33)

#: How similarities are folded into a single score
MODE_SUM = "sum"          # weighted mean - lenient, metrics compensate for one another
MODE_PRODUCT = "product"  # weighted geometric mean - a single dissenting metric is enough
MODES = {
    MODE_SUM: "sum (weighted mean)",
    MODE_PRODUCT: "product (strict)",
}


def classify(score, thresholds=DEFAULT_THRESHOLDS):
    """score ∈ [0,1] → 1/2/3, None → None."""
    if score is None:
        return None
    # None means "default thresholds": the caller often passes a value taken from
    # the settings, and that value may itself be None
    high, low = thresholds if thresholds else DEFAULT_THRESHOLDS
    if score >= high:
        return 1
    if score >= low:
        return 2
    return 3


def run_metrics(profile, candidate):
    """
    Compute ALL metrics (regardless of weights); this is the expensive part.

    candidate: a SpectralStats or a RasterSample.
    Returns (results, notes, candidate).
    """
    if not isinstance(candidate, SpectralStats):
        candidate = SpectralStats.from_sample(candidate)

    ok, message = profile.compatibility(candidate.band_names)
    if not ok:
        raise ValueError(f"The profile does not fit this raster: {message}")

    notes = []
    if profile.singular:
        notes.append("the profile covariance is singular, so the Mahalanobis metrics are unavailable")
    if candidate.n < profile.k:
        notes.append(f"the feature has only {candidate.n} px across {profile.k} band(s), "
                     "the assessment is unstable")

    results = {}
    for key in METRIC_ORDER:
        func = METRICS[key]
        try:
            results[key] = func(profile, candidate)
        except Exception as e:                      # a metric must never bring the whole calculation down
            results[key] = MetricResult(key, key, ok=False,
                                        note=f"{type(e).__name__}: {e}")
    return results, notes, candidate


def aggregate(results, weights=None, thresholds=DEFAULT_THRESHOLDS,
              mode=MODE_SUM, notes=None):
    """
    Fold the computed metrics into a single score. Cheap enough to call on every
    weight change in the dialog without recomputing the metrics.

    weights: {key: weight}; a weight of 0 means "show it but do not count it".
    """
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    thresholds = tuple(thresholds) if thresholds else DEFAULT_THRESHOLDS
    notes = list(notes or [])

    applied, total_weight = {}, 0.0
    weighted_sum, weighted_log = 0.0, 0.0
    for key, result in results.items():
        weight = float(weights.get(key, 0.0) or 0.0)
        if weight <= 0:
            continue
        if not result.ok:
            notes.append(f"metric \"{result.name}\" excluded: {result.note}")
            continue
        applied[key] = weight
        total_weight += weight
        weighted_sum += weight * result.similarity
        weighted_log += weight * math.log(max(result.similarity, 1e-6))

    if total_weight <= 0:
        notes.append("not a single enabled metric could be computed")
        return TypicalityResult(None, None, results, applied, thresholds, notes, mode)

    # the weight of excluded metrics is redistributed automatically (we divide by
    # the sum applied, not by the sum requested)
    if mode == MODE_PRODUCT:
        score = math.exp(weighted_log / total_weight)
    else:
        score = weighted_sum / total_weight
    return TypicalityResult(score, classify(score, thresholds), results, applied,
                            thresholds, notes, mode)


def evaluate(profile, candidate, weights=None, thresholds=DEFAULT_THRESHOLDS,
             mode=MODE_SUM):
    """Compute the metrics and fold them into an assessment straight away."""
    results, notes, _ = run_metrics(profile, candidate)
    return aggregate(results, weights, thresholds, mode, notes)
