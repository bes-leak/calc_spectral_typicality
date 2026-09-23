# -*- coding: utf-8 -*-
"""
histogram.py
Адаптивное разбиение на бины с ограниченным объёмом работы.

Почему не bins='fd' напрямую:
* при узком IQR и широком диапазоне (типично для 16-битных снимков с выбросами)
  правило Фридмана–Диакониса даёт миллионы бинов → MemoryError;
* уменьшение числа бинов по одному пересчитывает гистограмму по всему массиву
  десятки/сотни раз. Здесь число бинов сразу зажато в [MIN_BINS, MAX_BINS],
  а уменьшается геометрически — не больше ~12 проходов.
"""

import numpy as np

MIN_BINS = 8
MAX_BINS = 256


def _suggest_bin_count(values: np.ndarray, min_bins: int, max_bins: int) -> int:
    """Оценка числа бинов по Фридману–Диаконису с жёстким потолком."""
    n = values.size
    if n < 2:
        return min_bins

    lo, hi = float(np.min(values)), float(np.max(values))
    span = hi - lo
    if not np.isfinite(span) or span <= 0:
        return 1                      # все значения одинаковы

    q25, q75 = np.percentile(values, [25, 75])
    iqr = float(q75 - q25)
    width = 2.0 * iqr / (n ** (1.0 / 3.0)) if iqr > 0 else 0.0

    if width <= 0:
        # вырожденное распределение (почти все значения совпадают) → Стёрджес
        n_bins = int(np.ceil(np.log2(n) + 1))
    else:
        estimated = span / width
        if not np.isfinite(estimated) or estimated > max_bins:
            n_bins = max_bins
        else:
            n_bins = int(np.ceil(estimated))

    return int(min(max(n_bins, min_bins), max_bins))


def histogram(values, max_zeros: int = 0, step: int = 1,
              min_bins: int = MIN_BINS, max_bins: int = MAX_BINS):
    """
    Гистограмма с подбором числа бинов.

    max_zeros : сколько пустых бинов допустимо; пока их больше — число бинов
                уменьшается.
    step      : минимальный шаг уменьшения (сохранён для совместимости);
                фактический шаг — max(step, 25% от текущего числа бинов).

    Returns (counts, edges) — как np.histogram.
    """
    values = np.asarray(values, dtype=np.float64).ravel()
    values = values[np.isfinite(values)]
    if values.size == 0:
        return np.zeros(1, dtype=np.int64), np.array([0.0, 1.0])

    step = max(int(step), 1)
    min_bins = max(int(min_bins), 1)
    max_bins = max(int(max_bins), min_bins)

    n_bins = _suggest_bin_count(values, min_bins, max_bins)
    counts, edges = np.histogram(values, bins=n_bins)

    while int(np.count_nonzero(counts <= 0)) > max_zeros and n_bins > min_bins:
        n_bins = max(min_bins, min(n_bins - step, int(n_bins * 0.75)))
        counts, edges = np.histogram(values, bins=n_bins)

    return counts, edges
