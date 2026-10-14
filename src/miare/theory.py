"""
Music-theory primitives shared by the analysis and generation layers.

Rules that both `analysis.py` and `counterpoint.py` need live here so there is
exactly one copy of each.  Everything in this module is pure: no I/O, no
randomness, no dependency on any other `miare` module except `ir`.

Scope note: this module holds theory *facts* (where a measure is, what a scale
contains).  Voice-leading *rules* are a different boundary and belong in
`constraints.py` (DESIGN.md §17).  §17's module list predates this module and
does not name it; it stays a leaf both `analysis` and `generation` may import.
"""
from __future__ import annotations

from fractions import Fraction

from .ir import Measure, SourceScore


MAJOR_SCALE_INTERVALS = (0, 2, 4, 5, 7, 9, 11)
NATURAL_MINOR_SCALE_INTERVALS = (0, 2, 3, 5, 7, 8, 10)


def measure_for_time(score: SourceScore, time: Fraction) -> Measure:
    """The measure whose half-open span `[start, end)` contains `time`.

    Falls back to the last measure when no span matches, which covers both a
    time past the end of the score and a score whose first measure starts late.
    """
    for measure in score.measures:
        if measure.start <= time < measure.end:
            return measure
    return score.measures[-1]


def scale_intervals(mode: str) -> tuple[int, ...]:
    """Semitone offsets above the tonic for `mode`'s seven-note scale.

    Returns intervals, not pitch classes: callers that want pitch classes add
    their own tonic and take mod 12.
    """
    if mode.startswith("min"):
        return NATURAL_MINOR_SCALE_INTERVALS
    return MAJOR_SCALE_INTERVALS
