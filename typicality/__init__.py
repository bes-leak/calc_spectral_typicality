# -*- coding: utf-8 -*-
"""
Typicality of a feature's membership in its class.

Scale: 1 - typical, 3 - atypical, None - not set.
The lower the value, the more confident the membership.
"""

TYPICAL = 1
DOUBTFUL = 2
ATYPICAL = 3

VALUES = (TYPICAL, DOUBTFUL, ATYPICAL)

LABELS = {
    TYPICAL: "typical",
    DOUBTFUL: "doubtful",
    ATYPICAL: "atypical",
    None: "not set",
}


def label_for(value):
    """Human-readable name of the assessment."""
    return LABELS.get(value, "not set")


def is_valid(value):
    return value is None or value in VALUES


def normalize(value):
    """Coerce a value to 1/2/3/None, discarding anything else."""
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value in VALUES else None
