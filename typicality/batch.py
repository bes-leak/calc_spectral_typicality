# -*- coding: utf-8 -*-
"""
batch.py
Пакетная оценка типичности: прогон по объектам слоя с эталоном своего класса.

Здесь нет ничего от QGIS — доступ к геометрии и растру передаётся колбэками.
Так ядро прогона проверяется без карты и без интерфейса, а контроллер отвечает
только за диалоги и прогресс.
"""

import csv
from datetime import datetime

import numpy as np

from . import label_for
from .metrics import METRIC_ORDER, MODE_SUM, aggregate, run_metrics
from .profile import SpectralStats

SKIP_NO_PROFILE = "нет эталона для класса"
SKIP_NO_PIXELS = "нет пикселей растра"
SKIP_MANUAL = "оценка проставлена вручную"
SKIP_INCOMPATIBLE = "эталон не подходит к растру"


class BatchReport:
    """Результат прогона: строки по объектам и сводка."""

    def __init__(self):
        self.rows = []                 # успешно оценённые
        self.skipped = []              # [(key, class, причина)]
        self.cancelled = False

    # ------------------------------------------------------------------

    @property
    def processed(self):
        return len(self.rows)

    def counts_by_class(self):
        """{класс: {1: n, 2: n, 3: n}}"""
        result = {}
        for row in self.rows:
            bucket = result.setdefault(row["class"] or "—", {})
            bucket[row["value"]] = bucket.get(row["value"], 0) + 1
        return result

    def skip_reasons(self):
        result = {}
        for _, _, reason in self.skipped:
            result[reason] = result.get(reason, 0) + 1
        return result

    def summary_text(self):
        lines = []
        if self.cancelled:
            lines.append("⚠ Прогон прерван пользователем.")
        lines.append(f"Оценено объектов: {self.processed}")
        for class_name, counts in sorted(self.counts_by_class().items()):
            parts = ", ".join(f"{value} ({label_for(value)}): {counts[value]}"
                              for value in sorted(counts))
            lines.append(f"  • {class_name}: {parts}")
        if self.skipped:
            lines.append(f"Пропущено: {len(self.skipped)}")
            for reason, count in sorted(self.skip_reasons().items()):
                lines.append(f"  • {reason}: {count}")
        return "\n".join(lines)

    # ------------------------------------------------------------------

    def csv_header(self):
        header = ["fid", "key", "class", "etalon", "n_pixels",
                  "score", "value", "label", "mode", "thr_high", "thr_low"]
        for key in METRIC_ORDER:
            header += [f"{key}_raw", f"{key}_sim", f"{key}_weight"]
        header.append("notes")
        return header

    def csv_rows(self):
        for row in self.rows:
            values = [row["fid"], row["key"], row["class"], row["etalon"],
                      row["n_pixels"], round(row["score"], 6), row["value"],
                      label_for(row["value"]), row["mode"],
                      row["thresholds"][0], row["thresholds"][1]]
            metrics = row["metrics"]
            for key in METRIC_ORDER:
                metric = metrics.get(key) or {}
                values += [metric.get("raw"), metric.get("similarity"),
                           metric.get("weight")]
            values.append("; ".join(row.get("notes") or []))
            yield values

    def write_csv(self, path, delimiter=";"):
        """
        CSV для таблиц в работе. utf-8-sig и «;» — чтобы русский Excel открыл
        файл без плясок с кодировкой и разбил по столбцам.
        В pandas: pd.read_csv(path, sep=';')
        """
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            writer.writerow(self.csv_header())
            for values in self.csv_rows():
                writer.writerow(values)
        return path


def run_batch(items, profile_for, sample_for, weights=None, thresholds=None,
              mode="sum", skip_key=None, progress=None, is_cancelled=None):
    """
    Прогнать оценку по объектам.

    items       : [(feature, key, class_name)]
    profile_for : (feature) -> (ClassProfile | None, пояснение, класс)
    sample_for  : (feature) -> RasterSample | None
    skip_key    : (key) -> причина пропуска или None (например ручная оценка)
    progress    : (index, total, key) -> None
    is_cancelled: () -> bool

    Возвращает BatchReport; записью в хранилище занимается вызывающий код.
    """
    report = BatchReport()
    total = len(items)

    for index, (feature, key, class_name) in enumerate(items):
        if is_cancelled is not None and is_cancelled():
            report.cancelled = True
            break
        if progress is not None:
            progress(index, total, key)

        if skip_key is not None:
            reason = skip_key(key)
            if reason:
                report.skipped.append((key, class_name, reason))
                continue

        profile, _, detected_class = profile_for(feature)
        class_name = detected_class or class_name
        if profile is None:
            report.skipped.append((key, class_name, SKIP_NO_PROFILE))
            continue

        sample = sample_for(feature)
        if not sample:
            report.skipped.append((key, class_name, SKIP_NO_PIXELS))
            continue

        try:
            results, notes, candidate = run_metrics(profile, sample)
        except ValueError:                       # каналы не совпали
            report.skipped.append((key, class_name, SKIP_INCOMPATIBLE))
            continue

        outcome = aggregate(results, weights, thresholds, mode, notes)
        if outcome.value is None:
            report.skipped.append((key, class_name, "не удалось посчитать метрики"))
            continue

        payload = outcome.to_dict()
        report.rows.append({
            "fid": feature.id(),
            "key": key,
            "class": class_name,
            "etalon": profile.name,
            "n_pixels": candidate.n,
            "score": outcome.score,
            "value": outcome.value,
            "mode": outcome.mode,
            "thresholds": list(outcome.thresholds),
            "metrics": payload["metrics"],
            "notes": payload.get("notes"),
            "details": payload,
        })

    if progress is not None:
        progress(total, total, "")
    return report


# ---------------------------------------------------------------------------
# Матрица разделимости: каждый объект против ВСЕХ эталонов
# ---------------------------------------------------------------------------

SKIP_TOO_SMALL = "меньше минимального размера"


class SeparabilityReport:
    """Оценки каждого объекта по каждому эталону."""

    def __init__(self, names):
        self.names = list(names)      # эталоны в порядке колонок
        self.rows = []
        self.skipped = []
        self.cancelled = False

    @property
    def processed(self):
        return len(self.rows)

    # ------------------------------------------------------------------

    def classes(self):
        return sorted({row["class"] or "— без класса —" for row in self.rows})

    def matrix(self):
        """
        (классы, эталоны, медианы score, количества объектов).

        Медиана, а не среднее: распределение score сильно скошено, и один
        объект-выброс не должен решать за весь класс.
        """
        classes = self.classes()
        medians = np.full((len(classes), len(self.names)), np.nan)
        counts = np.zeros(len(classes), dtype=int)
        for i, class_name in enumerate(classes):
            scores = [row["scores"] for row in self.rows
                      if (row["class"] or "— без класса —") == class_name]
            counts[i] = len(scores)
            if not scores:
                continue
            block = np.array([[s.get(name, np.nan) for name in self.names]
                              for s in scores], dtype=np.float64)
            with np.errstate(all="ignore"):
                medians[i] = np.nanmedian(block, axis=0)
        return classes, list(self.names), medians, counts

    def own_etalon_wins(self):
        """Доля объектов, у которых свой эталон оказался лучшим, по классам."""
        result = {}
        for class_name in self.classes():
            rows = [r for r in self.rows
                    if (r["class"] or "— без класса —") == class_name]
            usable = [r for r in rows if r["own_score"] is not None]
            if not usable:
                result[class_name] = None
                continue
            wins = sum(1 for r in usable if r["best_etalon"] == r["class"])
            result[class_name] = wins / len(usable)
        return result

    def summary_text(self):
        lines = []
        if self.cancelled:
            lines.append("⚠ Прогон прерван пользователем.")
        lines.append(f"Объектов посчитано: {self.processed}")
        lines.append(f"Эталонов: {len(self.names)}")
        wins = self.own_etalon_wins()
        good = [(name, share) for name, share in wins.items() if share is not None]
        if good:
            lines.append("")
            lines.append("Свой эталон оказался лучшим:")
            for name, share in sorted(good, key=lambda item: item[1]):
                mark = "  ⚠" if share < 0.5 else "   "
                lines.append(f"{mark} {name}: {share:.0%}")
        if self.skipped:
            lines.append("")
            lines.append(f"Пропущено: {len(self.skipped)}")
            reasons = {}
            for _, _, reason in self.skipped:
                reasons[reason] = reasons.get(reason, 0) + 1
            for reason, count in sorted(reasons.items()):
                lines.append(f"  • {reason}: {count}")
        return "\n".join(lines)

    # ------------------------------------------------------------------

    def write_csv(self, path, delimiter=";"):
        """Подробная таблица: строка на объект, колонка на эталон."""
        header = (["fid", "key", "class", "n_pixels", "own_score",
                   "best_etalon", "best_score", "margin"]
                  + [f"score_{name}" for name in self.names])
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            writer.writerow(header)
            for row in self.rows:
                writer.writerow(
                    [row["fid"], row["key"], row["class"], row["n_pixels"],
                     _round(row["own_score"]), row["best_etalon"],
                     _round(row["best_score"]), _round(row["margin"])]
                    + [_round(row["scores"].get(name)) for name in self.names])
        return path

    def write_matrix_csv(self, path, delimiter=";"):
        """Сводка: медиана score по парам «класс объектов × эталон»."""
        classes, names, medians, counts = self.matrix()
        wins = self.own_etalon_wins()
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            writer.writerow(["класс_объектов", "объектов", "свой_эталон_лучший"]
                            + [f"эталон_{name}" for name in names])
            for i, class_name in enumerate(classes):
                share = wins.get(class_name)
                writer.writerow([class_name, int(counts[i]), _round(share)]
                                + [_round(v) for v in medians[i]])
        return path


def _round(value, digits=6):
    if value is None:
        return ""
    try:
        if np.isnan(value):
            return ""
    except TypeError:
        return value
    return round(float(value), digits)


def run_separability(items, profiles, sample_for, weights=None, thresholds=None,
                     mode=MODE_SUM, min_pixels=1, progress=None, is_cancelled=None):
    """
    Прогнать каждый объект по ВСЕМ эталонам.

    Пиксели объекта читаются и сводятся в статистику ОДИН раз, дальше
    переиспользуются для всех эталонов — иначе отдельный прогон на каждый
    эталон повторял бы самую дорогую часть столько раз, сколько классов.

    profiles: {имя: ClassProfile}
    """
    names = list(profiles)
    report = SeparabilityReport(names)
    total = len(items)

    for index, (feature, key, class_name) in enumerate(items):
        if is_cancelled is not None and is_cancelled():
            report.cancelled = True
            break
        if progress is not None:
            progress(index, total, key)

        sample = sample_for(feature)
        if not sample:
            report.skipped.append((key, class_name, SKIP_NO_PIXELS))
            continue
        if sample.n_pixels < min_pixels:
            report.skipped.append((key, class_name, SKIP_TOO_SMALL))
            continue

        stats = SpectralStats.from_sample(sample)      # среднее и ковариация — один раз
        scores = {}
        for name in names:
            profile = profiles[name]
            try:
                results, notes, _ = run_metrics(profile, stats)
            except ValueError:                          # каналы не совпали
                continue
            outcome = aggregate(results, weights, thresholds, mode, notes)
            if outcome.score is not None:
                scores[name] = outcome.score

        if not scores:
            report.skipped.append((key, class_name, SKIP_INCOMPATIBLE))
            continue

        best_etalon = max(scores, key=scores.get)
        best_score = scores[best_etalon]
        own_score = scores.get(class_name)
        report.rows.append({
            "fid": feature.id(),
            "key": key,
            "class": class_name,
            "n_pixels": stats.n,
            "scores": scores,
            "own_score": own_score,
            "best_etalon": best_etalon,
            "best_score": best_score,
            # насколько лучший чужой эталон обошёл свой: >0 — тревожный знак
            "margin": (best_score - own_score) if own_score is not None else None,
        })

    if progress is not None:
        progress(total, total, "")
    return report


def details_for_store(row, raster_name=None, profile_name=None, compact=True):
    """
    Детали автооценки для хранилища.

    compact=True оставляет от метрик только похожесть и вес: полная раскладка
    (сырые значения, единицы, примечания) весит около 1.4 КБ на объект, а
    хранилище целиком лежит внутри .qgz — на тысячах объектов это мегабайты в
    файле проекта. Полная раскладка остаётся в CSV пакетного прогона.
    """
    details = dict(row["details"])
    if compact:
        details["metrics"] = {
            key: {"similarity": metric.get("similarity"), "weight": metric.get("weight")}
            for key, metric in (details.get("metrics") or {}).items()
            if metric.get("weight")
        }
        details["metrics_compact"] = True
        details.pop("notes", None)
    details["class_profile"] = profile_name or row["etalon"]
    details["object_class"] = row["class"]
    details["profile_choice"] = f"по классу «{row['class']}»" if row["class"] else "пакетный прогон"
    details["n_pixels"] = row["n_pixels"]
    details["batch"] = True
    details["batch_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if raster_name:
        details["raster_layer"] = raster_name
    return details
