# -*- coding: utf-8 -*-
"""
metrics.py
Метрики похожести объекта на эталон класса и сведение их в одну оценку.

Каждая метрика возвращает «сырое» значение (в своих единицах) и похожесть в
[0, 1], где 1 — совпадение с эталоном. Итог: score = Σ wᵢ·simᵢ / Σ wᵢ по
включённым метрикам, которые удалось посчитать. Метрика, для которой данных не
хватило (например вырожденная ковариация у мелкого объекта), исключается с
пометкой, а её вес перераспределяется — молча выдавать мусор нельзя.

Замечание про Махаланобиса: расстояние считается относительно РАЗБРОСА класса
(Σ), а не стандартной ошибки среднего (Σ/n). Иначе при большом числе пикселей
любое, даже ничтожное, отличие среднего давало бы p≈0 и все объекты оказались
бы нетипичными. Здесь вопрос другой: «правдоподобно ли, что объект — это ещё
одна реализация того же класса».
"""

import math

import numpy as np

from .profile import SpectralStats, regularize_cov

try:                                    # scipy есть в QGIS, но не в каждой сборке
    from scipy.stats import chi2 as _chi2
except Exception:                       # pragma: no cover
    _chi2 = None


# ---------------------------------------------------------------------------
# χ² без обязательной scipy
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
    """P(X > x) для χ² с df степенями свободы."""
    if _chi2 is not None:
        return float(_chi2.sf(x, df))
    a, x = df / 2.0, x / 2.0
    if x <= 0:
        return 1.0
    return 1.0 - _gser(a, x) if x < a + 1.0 else _gcf(a, x)


def chi2_isf(p, df):
    """Квантиль: x, при котором P(X > x) = p."""
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
# Результаты
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
    """Итог автоопределения."""

    def __init__(self, score, value, metrics, weights, thresholds, notes=None,
                 mode="sum"):
        self.score = score
        self.value = value
        self.metrics = metrics                # {key: MetricResult}
        self.weights = weights                # {key: применённый вес}
        self.thresholds = thresholds
        self.notes = notes or []
        self.mode = mode

    def contributions(self):
        """{key: вклад метрики в итог} — для объяснения вердикта."""
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
# Метрики
# ---------------------------------------------------------------------------

#: Масштаб перевода спектрального угла в похожесть, радианы.
#: Линейная шкала 1 − α/(π/2) не годится: реальные углы между классами лежат в
#: 0.02–0.3 рад, то есть похожесть всегда получалась бы 0.98+, и SAM ничего не
#: различал. exp(−α/α₀) даёт 0.5 при α ≈ 0.10 рад — это привычный порог
#: «тот же класс» в дистанционном зондировании.
SAM_SCALE = 0.15


def spectral_angle(profile, cand, scale=SAM_SCALE):
    """
    SAM: угол между средними векторами.

    Инвариантен к яркости: если спектр объекта отличается от эталона только
    множителем (тень, освещённость), угол равен нулю и SAM даёт 1. Именно
    поэтому SAM в связке с Махаланобисом: первый ловит форму спектра, второй —
    абсолютные уровни.
    """
    a = np.asarray(profile.mean, dtype=np.float64)
    b = np.asarray(cand.mean, dtype=np.float64)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 0:
        return MetricResult("sam", "Спектральный угол (SAM)", ok=False,
                            note="нулевой вектор средних")
    cos = float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))
    angle = math.acos(cos)
    similarity = float(np.clip(math.exp(-angle / max(scale, 1e-9)), 0.0, 1.0))
    return MetricResult("sam", "Спектральный угол (SAM)", raw=angle, unit="рад",
                        similarity=similarity)


#: Порог попиксельного спектрального угла, радианы.
#: Шум отдельных пикселей раздувает угол на порядок относительно угла по
#: среднему спектру (у однородного объекта медиана ≈ 0.02 рад против 0.0007),
#: поэтому порог здесь свой и заметно мягче шкалы SAM по среднему.
SAM_PIXEL_THRESHOLD = 0.10


def spectral_angle_pixels(profile, cand, threshold=SAM_PIXEL_THRESHOLD):
    """
    Попиксельный SAM: доля пикселей объекта, чей спектр близок к эталону.

    SAM по среднему спектру не видит неоднородности: у объекта, собранного из
    двух непохожих спектров, среднее может случайно совпасть с эталоном, и угол
    окажется нулевым, хотя ни один пиксель на эталон не похож. Эта метрика
    смотрит на каждый пиксель и, в отличие от «доли своих пикселей», не зависит
    от яркости — ловит именно форму спектра.
    """
    reference = np.asarray(profile.mean, dtype=np.float64)
    ref_norm = float(np.linalg.norm(reference))
    if ref_norm <= 0:
        return MetricResult("sam_pixels", "SAM попиксельно (доля)", ok=False,
                            note="нулевой вектор средних у эталона")

    values = cand.values
    norms = np.linalg.norm(values, axis=1)
    valid = norms > 0
    if not np.any(valid):
        return MetricResult("sam_pixels", "SAM попиксельно (доля)", ok=False,
                            note="нет пикселей с ненулевым спектром")

    cos = np.clip(values[valid] @ reference / (norms[valid] * ref_norm), -1.0, 1.0)
    angles = np.arccos(cos)
    fraction = float(np.count_nonzero(angles <= threshold) / angles.size)
    median = float(np.median(angles))
    note = f"медиана {median:.3f} рад, порог {threshold:g}"
    if not np.all(valid):
        note += f", пропущено пикселей: {int(np.count_nonzero(~valid))}"
    return MetricResult("sam_pixels", "SAM попиксельно (доля)", raw=median, unit="рад",
                        similarity=fraction, note=note)


def mahalanobis_mean(profile, cand):
    """Расстояние Махаланобиса от среднего объекта до эталона → p-value χ²."""
    if profile.singular:
        return MetricResult("mahalanobis", "Махаланобис (p-value)", ok=False,
                            note="ковариация эталона вырождена")
    d2 = float(profile.mahalanobis_sq([cand.mean])[0])
    if not np.isfinite(d2):
        return MetricResult("mahalanobis", "Махаланобис (p-value)", ok=False,
                            note="расстояние не определено")
    p = float(chi2_sf(d2, profile.k))
    return MetricResult("mahalanobis", "Махаланобис (p-value)", raw=d2, unit="d²",
                        similarity=float(np.clip(p, 0.0, 1.0)))


def inlier_fraction(profile, cand, alpha=0.05):
    """Доля пикселей объекта внутри эллипсоида эталона уровня 1-alpha."""
    if profile.singular:
        return MetricResult("inliers", "Доля «своих» пикселей", ok=False,
                            note="ковариация эталона вырождена")
    threshold = chi2_isf(alpha, profile.k)
    d2 = profile.mahalanobis_sq(cand.values)
    d2 = d2[np.isfinite(d2)]
    if d2.size == 0:
        return MetricResult("inliers", "Доля «своих» пикселей", ok=False,
                            note="нет валидных пикселей")
    fraction = float(np.count_nonzero(d2 <= threshold) / d2.size)
    # в «сыром» значении показываем медианное d², иначе колонка дублировала бы
    # похожесть той же самой долей
    return MetricResult("inliers", "Доля «своих» пикселей", raw=float(np.median(d2)),
                        unit="d²", similarity=fraction,
                        note=f"доля {fraction:.3f} при пороге d² = {threshold:.2f} (α={alpha})")


def jeffries_matusita(profile, cand):
    """
    JM-расстояние между двумя гауссианами через расстояние Бхаттачарии.
    JM ∈ [0, 2]: 0 — распределения совпадают, 2 — полностью разделимы.
    """
    k = profile.k
    if cand.n <= k:
        return MetricResult("jm", "Jeffries–Matusita", ok=False,
                            note=f"пикселей {cand.n} ≤ каналов {k}: ковариация объекта не определена")
    cov1 = np.asarray(profile.cov_reg, dtype=np.float64)
    cov2, _, _ = regularize_cov(cand.cov, cand.n)
    mean_cov = 0.5 * (cov1 + cov2)

    delta = np.asarray(cand.mean) - np.asarray(profile.mean)
    try:
        term1 = 0.125 * float(delta @ np.linalg.inv(mean_cov) @ delta)
    except np.linalg.LinAlgError:
        return MetricResult("jm", "Jeffries–Matusita", ok=False,
                            note="средняя ковариация вырождена")

    sign_m, logdet_m = np.linalg.slogdet(mean_cov)
    sign_1, logdet_1 = np.linalg.slogdet(cov1)
    sign_2, logdet_2 = np.linalg.slogdet(cov2)
    if min(sign_m, sign_1, sign_2) <= 0:
        return MetricResult("jm", "Jeffries–Matusita", ok=False,
                            note="неположительно определённая ковариация")

    term2 = 0.5 * (logdet_m - 0.5 * (logdet_1 + logdet_2))
    bhattacharyya = max(term1 + term2, 0.0)
    jm = 2.0 * (1.0 - math.exp(-bhattacharyya))
    return MetricResult("jm", "Jeffries–Matusita", raw=jm, unit="JM",
                        similarity=float(np.clip(1.0 - jm / 2.0, 0.0, 1.0)))


def histogram_intersection(profile, cand):
    """
    Пересечение поканальных гистограмм на СЕТКЕ ЭТАЛОНА.

    Доли считаются от полного числа пикселей объекта, поэтому пиксели, вышедшие
    за диапазон эталона, честно уменьшают похожесть, а не выпадают из расчёта.
    """
    if not profile.histograms:
        return MetricResult("hist", "Пересечение гистограмм", ok=False,
                            note="в эталоне нет гистограмм")
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
        return MetricResult("hist", "Пересечение гистограмм", ok=False,
                            note="не удалось сопоставить каналы")
    value = float(np.mean(scores))
    return MetricResult("hist", "Пересечение гистограмм", raw=value, unit="",
                        similarity=value, note=f"усреднено по {len(scores)} канал(ам)")


# ---------------------------------------------------------------------------
# Реестр и агрегация
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

#: Метрики «по среднему» смотрят на сигнатуру объекта целиком, «попиксельно» —
#: на каждый пиксель. Это разные свидетельства, и веса делятся между группами,
#: а не между метриками: иначе две коррелированные метрики по среднему
#: перевешивают попиксельные и неоднородный объект проходит как типичный.
MEAN_BASED = ("sam", "mahalanobis", "jm", "hist")
PIXEL_BASED = ("sam_pixels", "inliers")

METRIC_GROUPS = {key: ("по среднему" if key in MEAN_BASED else "попиксельно")
                 for key in METRIC_ORDER}

DEFAULT_WEIGHTS = {
    "sam": 0.25,            # ┐ по среднему — 0.5
    "mahalanobis": 0.25,    # ┘
    "sam_pixels": 0.25,     # ┐ попиксельно — 0.5
    "inliers": 0.25,        # ┘
    "jm": 0.0,
    "hist": 0.0,
}

# score ≥ 0.66 → 1 (типичный); ≥ 0.33 → 2; иначе 3
DEFAULT_THRESHOLDS = (0.66, 0.33)

#: Способ свёртки похожестей в один score
MODE_SUM = "sum"          # взвешенное среднее — мягкий, метрики компенсируют друг друга
MODE_PRODUCT = "product"  # взвешенное среднее геометрическое — хватает одного «против»
MODES = {
    MODE_SUM: "сумма (взвешенное среднее)",
    MODE_PRODUCT: "произведение (строгая)",
}


def classify(score, thresholds=DEFAULT_THRESHOLDS):
    """score ∈ [0,1] → 1/2/3, None → None."""
    if score is None:
        return None
    # None означает «пороги по умолчанию»: вызывающий код часто передаёт
    # значение из настроек, а там может лежать None
    high, low = thresholds if thresholds else DEFAULT_THRESHOLDS
    if score >= high:
        return 1
    if score >= low:
        return 2
    return 3


def run_metrics(profile, candidate):
    """
    Посчитать ВСЕ метрики (независимо от весов) — это самая дорогая часть.

    candidate: SpectralStats либо RasterSample.
    Returns (results, notes, candidate).
    """
    if not isinstance(candidate, SpectralStats):
        candidate = SpectralStats.from_sample(candidate)

    ok, message = profile.compatibility(candidate.band_names)
    if not ok:
        raise ValueError(f"Эталон не подходит к этому растру: {message}")

    notes = []
    if profile.singular:
        notes.append("ковариация эталона вырождена — метрики Махаланобиса недоступны")
    if candidate.n < profile.k:
        notes.append(f"у объекта всего {candidate.n} пикс. на {profile.k} канал(ов) — "
                     "оценка неустойчива")

    results = {}
    for key in METRIC_ORDER:
        func = METRICS[key]
        try:
            results[key] = func(profile, candidate)
        except Exception as e:                      # метрика не должна ронять расчёт
            results[key] = MetricResult(key, key, ok=False,
                                        note=f"{type(e).__name__}: {e}")
    return results, notes, candidate


def aggregate(results, weights=None, thresholds=DEFAULT_THRESHOLDS,
              mode=MODE_SUM, notes=None):
    """
    Свести посчитанные метрики в один score. Дёшево — можно звать на каждое
    изменение веса в диалоге, не пересчитывая метрики.

    weights: {key: вес}; вес 0 означает «показать, но не учитывать».
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
            notes.append(f"метрика «{result.name}» исключена: {result.note}")
            continue
        applied[key] = weight
        total_weight += weight
        weighted_sum += weight * result.similarity
        weighted_log += weight * math.log(max(result.similarity, 1e-6))

    if total_weight <= 0:
        notes.append("не удалось посчитать ни одной включённой метрики")
        return TypicalityResult(None, None, results, applied, thresholds, notes, mode)

    # вес исключённых метрик перераспределяется автоматически (делим на сумму
    # применённых, а не на сумму заданных)
    if mode == MODE_PRODUCT:
        score = math.exp(weighted_log / total_weight)
    else:
        score = weighted_sum / total_weight
    return TypicalityResult(score, classify(score, thresholds), results, applied,
                            thresholds, notes, mode)


def evaluate(profile, candidate, weights=None, thresholds=DEFAULT_THRESHOLDS,
             mode=MODE_SUM):
    """Посчитать метрики и сразу свести их в оценку."""
    results, notes, _ = run_metrics(profile, candidate)
    return aggregate(results, weights, thresholds, mode, notes)
