"""
Unit tests for the shared music-theory helpers in `miare.theory`.

`measure_for_time` and `scale_intervals` were each written twice, once in
`analysis.py` and once in `counterpoint.py`.  These tests pin the shared
behaviour so the two callers cannot drift apart again.
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from miare import theory
from miare.ir import Measure, SourceScore


def _score(measure_starts: list[int], length: int = 4) -> SourceScore:
    measures = [
        Measure(
            index=index,
            number=str(index + 1),
            start=Fraction(start),
            duration=Fraction(length),
            beats=4,
            beat_type=4,
        )
        for index, start in enumerate(measure_starts)
    ]
    return SourceScore(title="test", parts=[], measures=measures)


class TestMeasureForTime:
    def test_returns_the_measure_containing_the_time(self):
        score = _score([0, 4, 8])
        assert theory.measure_for_time(score, Fraction(0)).index == 0
        assert theory.measure_for_time(score, Fraction(3, 2)).index == 0
        assert theory.measure_for_time(score, Fraction(4)).index == 1
        assert theory.measure_for_time(score, Fraction(9)).index == 2

    def test_measure_end_belongs_to_the_next_measure(self):
        """The interval is half-open: start <= time < end."""
        score = _score([0, 4])
        assert theory.measure_for_time(score, Fraction(4)).index == 1

    def test_time_past_the_last_measure_falls_back_to_the_last(self):
        score = _score([0, 4])
        assert theory.measure_for_time(score, Fraction(99)).index == 1

    def test_time_before_the_first_measure_falls_back_to_the_last(self):
        """Documents the original behaviour: no match at all -> last measure."""
        score = _score([4, 8])
        assert theory.measure_for_time(score, Fraction(0)).index == 1


class TestScaleIntervals:
    def test_major_intervals(self):
        assert theory.scale_intervals("major") == (0, 2, 4, 5, 7, 9, 11)

    def test_minor_intervals(self):
        assert theory.scale_intervals("minor") == (0, 2, 3, 5, 7, 8, 10)

    @pytest.mark.parametrize("mode", ["min", "minor", "mindless-but-starts-with-min"])
    def test_any_mode_starting_with_min_is_minor(self, mode):
        assert theory.scale_intervals(mode) == (0, 2, 3, 5, 7, 8, 10)

    @pytest.mark.parametrize("mode", ["major", "maj", "dorian", ""])
    def test_everything_else_is_major(self, mode):
        assert theory.scale_intervals(mode) == (0, 2, 4, 5, 7, 9, 11)

    def test_returns_intervals_not_pitch_classes(self):
        """The tonic arithmetic stays at the call site, not in this helper."""
        assert theory.scale_intervals("major")[0] == 0


class TestCallersDelegate:
    def test_analysis_scale_delegates_to_shared_helper(self):
        from miare import analysis

        for mode in ("major", "minor"):
            assert analysis._scale(mode) == theory.scale_intervals(mode)

    def test_counterpoint_tonal_pitch_classes_adds_the_tonic(self):
        """The counterpoint caller owns the tonic arithmetic; theory owns the intervals."""
        from dataclasses import replace

        from miare.analysis import analyze_score
        from miare.counterpoint import _tonal_pitch_classes
        from miare.musicxml import parse_musicxml

        fixture = Path(__file__).parent / "fixtures" / "simple_melody.musicxml"
        song = analyze_score(parse_musicxml(fixture))

        for tonic_pc in (0, 7, 11):
            for mode in ("major", "minor"):
                shifted = replace(song, tonic_pc=tonic_pc, mode=mode)
                expected = {
                    (tonic_pc + interval) % 12
                    for interval in theory.scale_intervals(mode)
                }
                assert _tonal_pitch_classes(shifted) == expected

    def test_duplicated_private_helpers_are_gone(self):
        from miare import analysis, counterpoint

        assert not hasattr(analysis, "_measure_for_time")
        assert not hasattr(counterpoint, "_measure_at")
