"""
Unit tests for the shared voice-leading rules in `miare.constraints`.

Four relationships between a generated voice and the primary melody were
written twice, once in `generation.py` and once in `counterpoint.py`, and
three of them had drifted apart in *strength*:

| Rule             | counterpoint.py | generation.py | resolved to  |
| ---------------- | --------------- | ------------- | ------------ |
| leap over octave | hard rejection  | `-4.0`        | hard in both |
| contrary motion  | `+1.1`          | `+1.2`        | `+1.1`       |
| voice crossing   | `-3.0`          | `-5.0`        | `-3.0`       |
| parallel perfect | hard rejection  | hard rejection| hard in both |

DESIGN.md §3.5 separates constraints from preferences: a constraint violation
blocks output, a preference only ranks.  The leap limit is a constraint, so
scoring it away was the bug, not the hard ban.  These tests pin the shared
definitions so the two callers cannot drift again.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

from miare import constraints


class TestPerfectIntervalClasses:
    def test_unison_and_fifth_are_the_perfect_classes(self):
        assert constraints.PERFECT_INTERVAL_CLASSES == {0, 7}

    def test_counterpoint_reads_the_shared_set(self):
        from miare import counterpoint

        assert counterpoint.PERFECT_INTERVAL_CLASSES is (
            constraints.PERFECT_INTERVAL_CLASSES
        )


class TestIsLeapOverOctave:
    """DESIGN.md §3.5: a constraint blocks output rather than being scored."""

    @pytest.mark.parametrize("motion", [13, -13, 24, -24])
    def test_motion_wider_than_an_octave_is_a_violation(self, motion):
        assert constraints.is_leap_over_octave(motion) is True

    @pytest.mark.parametrize("motion", [0, 1, -7, 12, -12])
    def test_an_octave_or_less_is_allowed(self, motion):
        assert constraints.is_leap_over_octave(motion) is False

    def test_the_limit_is_an_octave_inclusive(self):
        assert constraints.is_leap_over_octave(12) is False
        assert constraints.is_leap_over_octave(13) is True


class TestIsParallelPerfect:
    def test_similar_motion_between_two_perfect_intervals_is_parallel(self):
        assert constraints.is_parallel_perfect(
            previous_interval_class=7,
            interval_class=7,
            melody_motion=2,
            voice_motion=2,
        ) is True

    def test_both_voices_falling_is_also_similar_motion(self):
        assert constraints.is_parallel_perfect(
            previous_interval_class=0,
            interval_class=7,
            melody_motion=-2,
            voice_motion=-1,
        ) is True

    def test_contrary_motion_into_a_perfect_interval_is_allowed(self):
        assert constraints.is_parallel_perfect(
            previous_interval_class=7,
            interval_class=7,
            melody_motion=2,
            voice_motion=-2,
        ) is False

    def test_a_static_voice_is_not_parallel_motion(self):
        assert constraints.is_parallel_perfect(
            previous_interval_class=7,
            interval_class=7,
            melody_motion=0,
            voice_motion=2,
        ) is False

    @pytest.mark.parametrize("imperfect", [3, 4, 5, 9])
    def test_an_imperfect_interval_on_either_side_is_allowed(self, imperfect):
        assert constraints.is_parallel_perfect(
            previous_interval_class=imperfect,
            interval_class=7,
            melody_motion=2,
            voice_motion=2,
        ) is False
        assert constraints.is_parallel_perfect(
            previous_interval_class=7,
            interval_class=imperfect,
            melody_motion=2,
            voice_motion=2,
        ) is False

    def test_a_missing_previous_interval_is_not_a_violation(self):
        """The first slot has nothing to be parallel to."""
        assert constraints.is_parallel_perfect(
            previous_interval_class=None,
            interval_class=7,
            melody_motion=2,
            voice_motion=2,
        ) is False


class TestPreferenceWeights:
    """The three coefficients that had drifted, now one copy each."""

    def test_contrary_motion_bonus(self):
        assert constraints.CONTRARY_MOTION_BONUS == 1.1

    def test_voice_crossing_penalty(self):
        assert constraints.VOICE_CROSSING_PENALTY == -3.0

    def test_the_crossing_weight_is_a_penalty_and_the_motion_one_a_bonus(self):
        assert constraints.VOICE_CROSSING_PENALTY < 0 < (
            constraints.CONTRARY_MOTION_BONUS
        )


class TestIsContraryMotion:
    @pytest.mark.parametrize(
        "melody_motion,voice_motion",
        [(2, -2), (-3, 1), (12, -1)],
    )
    def test_opposite_directions_are_contrary(self, melody_motion, voice_motion):
        assert constraints.is_contrary_motion(melody_motion, voice_motion) is True

    @pytest.mark.parametrize(
        "melody_motion,voice_motion",
        [(2, 2), (-3, -1), (0, 2), (2, 0), (0, 0)],
    )
    def test_same_direction_or_a_static_voice_is_not(
        self, melody_motion, voice_motion
    ):
        assert constraints.is_contrary_motion(melody_motion, voice_motion) is False


class TestIsVoiceCrossing:
    def test_the_voice_at_or_above_the_melody_is_crossing(self):
        assert constraints.is_voice_crossing(midi=72, melody_midi=72) is True
        assert constraints.is_voice_crossing(midi=74, melody_midi=72) is True

    def test_the_voice_below_the_melody_is_not(self):
        assert constraints.is_voice_crossing(midi=71, melody_midi=72) is False


class TestTheLeapBanIsLoadBearingInTheSupportDP:
    """The leap limit is the rule that had diverged, so pin that it now bites.

    `tests/test_voice_leading.py::test_support_leap_never_exceeds_octave`
    checks the chosen path, which the old `-4.0` penalty also happened to
    satisfy on this fixture.  These two pin the stronger claim: wide leaps are
    *available* to the DP in quantity, and every one of them is now rejected
    rather than scored, so no emission bonus can buy one.
    """

    def _support_spans(self):
        from pathlib import Path

        from miare.analysis import analyze_score
        from miare.generation import _support_spans
        from miare.instruments import get_profile
        from miare.musicxml import parse_musicxml

        fixture = Path(__file__).parent / "fixtures" / "simple_melody.musicxml"
        song = analyze_score(parse_musicxml(fixture))
        return _support_spans(song, get_profile("cello"))

    def test_wide_leaps_are_available_to_the_search(self):
        """Guard the guard: a rule that nothing can break proves nothing."""
        spans = self._support_spans()
        over_octave = sum(
            1
            for left, right in zip(spans, spans[1:])
            for previous in left["candidates"]
            for midi in right["candidates"]
            if constraints.is_leap_over_octave(midi - previous)
        )
        assert over_octave > 100, (
            f"only {over_octave} candidate transitions leap over an octave; "
            "the fixture no longer exercises the leap constraint"
        )

    def test_a_wide_leap_survives_no_emission_bonus(self, monkeypatch):
        """The whole point of a constraint: no score can outbid it.

        Drives the real `generate_support_voice` with the candidate list of
        every span forced to one pitch, alternating low and high so that every
        transition on offer leaps an octave and a half.  A `-4.0` penalty would
        pick the least bad path and return a voice; a hard ban leaves the DP no
        path at all, and it raises.
        """
        from pathlib import Path

        from miare import generation
        from miare.analysis import analyze_score
        from miare.instruments import get_profile
        from miare.musicxml import parse_musicxml

        fixture = Path(__file__).parent / "fixtures" / "simple_melody.musicxml"
        song = analyze_score(parse_musicxml(fixture))
        profile = get_profile("cello")
        real_spans = generation._support_spans

        def _only_illegal_leaps(song_arg, profile_arg):
            spans = real_spans(song_arg, profile_arg)
            assert len(spans) > 1, "need at least one transition to constrain"
            for index, span in enumerate(spans):
                span["candidates"] = [
                    profile_arg.min_midi + (0 if index % 2 == 0 else 18)
                ]
            return spans

        monkeypatch.setattr(generation, "_support_spans", _only_illegal_leaps)
        with pytest.raises(ValueError, match="no valid path"):
            generation.generate_support_voice(song, profile, seed=1)


class TestBothGeneratorsReadTheSharedRules:
    """The point of `constraints.py` is exactly one copy of each rule.

    These pin the *rule bodies*, so a future copy under a new name is caught
    too — the same guard `tests/test_shared_theory.py` puts on `theory.py`.
    """

    GENERATORS = ("generation", "counterpoint")
    PROBES = (
        ("{0, 7}", "read constraints.PERFECT_INTERVAL_CLASSES"),
        ("1.2, \"contrary motion\"", "add constraints.CONTRARY_MOTION_BONUS"),
        ("-5.0", "add constraints.VOICE_CROSSING_PENALTY"),
        ("value -= 4.0", "reject via constraints.is_leap_over_octave"),
    )

    @pytest.mark.parametrize("module_name", GENERATORS)
    @pytest.mark.parametrize("literal,instead", PROBES)
    def test_no_generator_inlines_a_shared_rule(
        self, module_name, literal, instead
    ):
        source = inspect.getsource(importlib.import_module(f"miare.{module_name}"))
        assert literal not in source, (
            f"miare.{module_name} inlines `{literal}`; {instead} instead"
        )

    @pytest.mark.parametrize("module_name", GENERATORS)
    def test_each_generator_imports_the_shared_module(self, module_name):
        module = importlib.import_module(f"miare.{module_name}")
        source = inspect.getsource(module)
        assert "from .constraints import" in source, (
            f"miare.{module_name} does not read miare.constraints"
        )

    def test_constraints_owns_the_weights(self):
        """Guard the guard: the probes must still match the real module."""
        source = inspect.getsource(constraints)
        assert "1.1" in source
        assert "-3.0" in source

    def test_constraints_imports_no_generator(self):
        """The shared module is a leaf: rules in, no generator out."""
        source = inspect.getsource(constraints)
        for module_name in self.GENERATORS:
            assert f"from .{module_name}" not in source
            assert f"import {module_name}" not in source
