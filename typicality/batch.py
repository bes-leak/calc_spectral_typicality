# -*- coding: utf-8 -*-
"""
batch.py
Batch typicality assessment: a run over the features of a layer, each against
the profile of its own class.

There is nothing QGIS-specific here - access to geometry and raster is passed in
as callbacks. That way the core of the run can be tested without a map and
without an interface, and the controller is left responsible only for dialogs
and progress.
"""

import csv
from datetime import datetime

import numpy as np

from . import label_for
from .metrics import METRIC_ORDER, MODE_SUM, aggregate, run_metrics
from .profile import SpectralStats

SKIP_NO_PROFILE = "no profile for this class"
SKIP_NO_PIXELS = "no raster pixels"
SKIP_MANUAL = "assessed manually"
SKIP_INCOMPATIBLE = "the profile does not fit the raster"


class BatchReport:
    """The result of a run: per-feature rows plus a summary."""

    def __init__(self):
        self.rows = []                 # successfully assessed
        self.skipped = []              # [(key, class, reason)]
        self.cancelled = False

    # ------------------------------------------------------------------

    @property
    def processed(self):
        return len(self.rows)

    def counts_by_class(self):
        """{class: {1: n, 2: n, 3: n}}"""
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
            lines.append("Run cancelled by the user.")
        lines.append(f"Features assessed: {self.processed}")
        for class_name, counts in sorted(self.counts_by_class().items()):
            parts = ", ".join(f"{value} ({label_for(value)}): {counts[value]}"
                              for value in sorted(counts))
            lines.append(f"  • {class_name}: {parts}")
        if self.skipped:
            lines.append(f"Skipped: {len(self.skipped)}")
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
        CSV for downstream work. utf-8-sig and ";" so that a localized Excel
        opens the file without an encoding dance and splits it into columns.
        In pandas: pd.read_csv(path, sep=';')
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
    Run the assessment over a set of features.

    items       : [(feature, key, class_name)]
    profile_for : (feature) -> (ClassProfile | None, explanation, class)
    sample_for  : (feature) -> RasterSample | None
    skip_key    : (key) -> reason to skip, or None (a manual assessment, say)
    progress    : (index, total, key) -> None
    is_cancelled: () -> bool

    Returns a BatchReport; writing to the store is the caller's job.
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
        except ValueError:                       # the bands did not match
            report.skipped.append((key, class_name, SKIP_INCOMPATIBLE))
            continue

        outcome = aggregate(results, weights, thresholds, mode, notes)
        if outcome.value is None:
            report.skipped.append((key, class_name, "the metrics could not be computed"))
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
# Separability matrix: every feature against EVERY profile
# ---------------------------------------------------------------------------

SKIP_TOO_SMALL = "smaller than the minimum size"


class SeparabilityReport:
    """The score of every feature against every profile."""

    def __init__(self, names):
        self.names = list(names)      # profiles in column order
        self.rows = []
        self.skipped = []
        self.cancelled = False

    @property
    def processed(self):
        return len(self.rows)

    # ------------------------------------------------------------------

    def classes(self):
        return sorted({row["class"] or "- no class -" for row in self.rows})

    def matrix(self):
        """
        (classes, profiles, median scores, feature counts).

        Median rather than mean: the score distribution is strongly skewed, and
        one outlying feature must not speak for a whole class.
        """
        classes = self.classes()
        medians = np.full((len(classes), len(self.names)), np.nan)
        counts = np.zeros(len(classes), dtype=int)
        for i, class_name in enumerate(classes):
            scores = [row["scores"] for row in self.rows
                      if (row["class"] or "- no class -") == class_name]
            counts[i] = len(scores)
            if not scores:
                continue
            block = np.array([[s.get(name, np.nan) for name in self.names]
                              for s in scores], dtype=np.float64)
            with np.errstate(all="ignore"):
                medians[i] = np.nanmedian(block, axis=0)
        return classes, list(self.names), medians, counts

    def own_etalon_wins(self):
        """Share of features whose own profile scored best, by class."""
        result = {}
        for class_name in self.classes():
            rows = [r for r in self.rows
                    if (r["class"] or "- no class -") == class_name]
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
            lines.append("Run cancelled by the user.")
        lines.append(f"Features computed: {self.processed}")
        lines.append(f"Class profiles: {len(self.names)}")
        wins = self.own_etalon_wins()
        good = [(name, share) for name, share in wins.items() if share is not None]
        if good:
            lines.append("")
            lines.append("Own profile scored best:")
            for name, share in sorted(good, key=lambda item: item[1]):
                mark = "  ⚠" if share < 0.5 else "   "
                lines.append(f"{mark} {name}: {share:.0%}")
        if self.skipped:
            lines.append("")
            lines.append(f"Skipped: {len(self.skipped)}")
            reasons = {}
            for _, _, reason in self.skipped:
                reasons[reason] = reasons.get(reason, 0) + 1
            for reason, count in sorted(reasons.items()):
                lines.append(f"  • {reason}: {count}")
        return "\n".join(lines)

    # ------------------------------------------------------------------

    def write_csv(self, path, delimiter=";"):
        """Detailed table: one row per feature, one column per profile."""
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
        """Summary: median score per (feature class x profile) pair."""
        classes, names, medians, counts = self.matrix()
        wins = self.own_etalon_wins()
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=delimiter)
            writer.writerow(["feature_class", "features", "own_profile_best"]
                            + [f"profile_{name}" for name in names])
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
    Run every feature against EVERY profile.

    A feature's pixels are read and reduced to statistics ONCE and then reused
    for all the profiles; a separate run per profile would otherwise repeat the
    most expensive part as many times as there are classes.

    profiles: {name: ClassProfile}
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

        stats = SpectralStats.from_sample(sample)      # mean and covariance, computed once
        scores = {}
        for name in names:
            profile = profiles[name]
            try:
                results, notes, _ = run_metrics(profile, stats)
            except ValueError:                          # the bands did not match
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
            # by how much the best foreign profile beat the own one: >0 is a warning sign
            "margin": (best_score - own_score) if own_score is not None else None,
        })

    if progress is not None:
        progress(total, total, "")
    return report


def details_for_store(row, raster_name=None, profile_name=None, compact=True):
    """
    Details of an automatic assessment, for the store.

    compact=True keeps only the similarity and the weight of each metric: the
    full breakdown (raw values, units, notes) weighs about 1.4 KB per feature,
    and the whole store lives inside the .qgz - over thousands of features that
    is megabytes in the project file. The full breakdown stays in the batch CSV.
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
    details["profile_choice"] = f"against class \"{row['class']}\"" if row["class"] else "batch run"
    details["n_pixels"] = row["n_pixels"]
    details["batch"] = True
    details["batch_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if raster_name:
        details["raster_layer"] = raster_name
    return details
