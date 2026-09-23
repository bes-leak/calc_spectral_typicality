# -*- coding: utf-8 -*-
"""
Оценка типичности принадлежности объекта к классу.

Шкала (согласована 28.07.2026): 1 — типичный, 3 — нетипичный, None — не указано.
Меньше значение — увереннее принадлежность классу.
"""

TYPICAL = 1
DOUBTFUL = 2
ATYPICAL = 3

VALUES = (TYPICAL, DOUBTFUL, ATYPICAL)

LABELS = {
    TYPICAL: "типичный",
    DOUBTFUL: "сомнительный",
    ATYPICAL: "нетипичный",
    None: "не указано",
}


def label_for(value):
    """Человекочитаемое название оценки."""
    return LABELS.get(value, "не указано")


def is_valid(value):
    return value is None or value in VALUES


def normalize(value):
    """Привести значение к 1/2/3/None, отбросив мусор."""
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value in VALUES else None
