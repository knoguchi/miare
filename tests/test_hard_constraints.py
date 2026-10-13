"""
Hard-constraint gate tests: the rules that make a candidate pitch unusable,
called directly instead of inferred from a finished arrangement.

Every rule here is hard (structurally impossible in output, seed-independent).
Preference arithmetic lives in `_score_action` and is not tested here.

The gate is pure: given a slot, a beam state, a candidate pitch and the
precomputed slot context it returns a `Rejection` or `None`. No arrangement
round trip, no analysis, no RNG.

All eleven rules are covered. The first five — the rest ban, the tonality ban
and the three owed-resolution rules — were the scorer's lines 552, 586, 598,
600 and 602. The six state-dependent ones (strong- and weak-beat dissonance,
the protected-phrase dissonance ban, the octave leap cap, parallel perfects
and protected voice crossing) were lines 609, 615, 624, 629, 659 and 672, and
QA added their tests. Each rule gets a rejection case and a near-miss case
that must pass, so a rule that stopped firing fails a test instead of going
quiet.

`tests/test_voice_leading.py` keeps the arrangement-level checks: they show
the beam search honours the gate, not that any one rule works.
"""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from miare.counterpoint import (
    BeamState,
    CandidateContext,
    CounterpointSlot,
    Rejection,
    SlotContext,
    check_hard_constraints,
)
from miare.instruments import get_profile


CELLO = get_profile("cello")

C_MAJOR_PCS = frozenset({0, 2, 4, 5, 7, 9, 11})


SLOT = CounterpointSlot(
    index=0,
    start=Fraction(0),
    duration=Fraction(1),
    phrase_id="p1",
    metric_strength=0.5,
    melody_activity=1,
    texture="counterpoint",
    cadence=False,
    identity_policy="free",
    structural_importance=0.5,
)

CONTEXT = SlotContext(
    melody_midi=72,
    source_pcs=frozenset(),
    source_midis=(),
    source_ids=(),
    source_bass_midi=None,
    harmony_pcs=frozenset({0, 4, 7}),
    harmony_root=0,
    tonal_pcs=C_MAJOR_PCS,
    future_melody=None,
    phrase_start=Fraction(0),
    phrase_end=Fraction(8),
    desired_register=60,
    motif_intervals=frozenset(),
)


def _slot(**overrides) -> CounterpointSlot:
    return replace(SLOT, **overrides)


def _context(**overrides) -> SlotContext:
    return replace(CONTEXT, **overrides)


def _check(
    slot: CounterpointSlot,
    state: BeamState,
    midi: int | None,
    context: SlotContext,
) -> Rejection | None:
    candidate = CandidateContext.build(slot, state, midi, context)
    return check_hard_constraints(CELLO, slot, state, midi, context, candidate)


# ---------------------------------------------------------------------------
# The five state-free rules (the scorer's old lines 552, 586, 598, 600, 602)
# ---------------------------------------------------------------------------

class TestRestRules:
    """A rest is banned where the line owes a resolution or ends a phrase."""

    def test_rest_allowed_on_an_ordinary_slot(self) -> None:
        assert _check(_slot(), BeamState(), None, _context()) is None

    def test_rest_rejected_while_a_dissonance_is_unresolved(self) -> None:
        state = BeamState(previous_midi=60, pending_resolution=-1)
        rejection = _check(_slot(), state, None, _context())
        assert rejection is not None
        assert rejection.rule == "rest-owes-resolution"

    def test_rest_rejected_at_a_cadence(self) -> None:
        rejection = _check(_slot(cadence=True), BeamState(), None, _context())
        assert rejection is not None
        assert rejection.rule == "rest-owes-resolution"


class TestTonalityRule:
    """With no source voices sounding, the pitch must belong to the key."""

    def test_in_key_pitch_passes(self) -> None:
        # MIDI 60 = C, in C major.
        assert _check(_slot(), BeamState(), 60, _context()) is None

    def test_chromatic_pitch_rejected_without_source_support(self) -> None:
        # MIDI 61 = C#, outside C major.
        rejection = _check(_slot(), BeamState(), 61, _context())
        assert rejection is not None
        assert rejection.rule == "non-tonal-pitch"

    def test_chromatic_pitch_allowed_when_source_voices_sound(self) -> None:
        context = _context(source_pcs=frozenset({1, 5}))
        # 61 against melody 72 is dissonant, so it also needs a stepwise
        # approach — the point here is that the key no longer bars it.
        state = BeamState(previous_midi=60)
        assert _check(_slot(), state, 61, context) is None


class TestPendingResolutionRules:
    """An owed resolution must be consonant, stepwise and in its direction."""

    def test_downward_resolution_passes(self) -> None:
        state = BeamState(previous_midi=62, pending_resolution=-1)
        # 60 against melody 72 → interval class 0, consonant; motion -2.
        assert _check(_slot(), state, 60, _context()) is None

    def test_dissonant_resolution_rejected(self) -> None:
        state = BeamState(previous_midi=60, pending_resolution=-1)
        rejection = _check(_slot(), state, 59, _context())
        assert rejection is not None
        assert rejection.rule == "resolution-not-consonant-step"

    def test_leaping_resolution_rejected(self) -> None:
        state = BeamState(previous_midi=72, pending_resolution=-1)
        # 65 is consonant against melody 72 (interval class 7) but the leap is 7.
        rejection = _check(_slot(), state, 65, _context())
        assert rejection is not None
        assert rejection.rule == "resolution-not-consonant-step"

    def test_resolution_without_a_previous_note_rejected(self) -> None:
        state = BeamState(previous_midi=None, pending_resolution=-1)
        rejection = _check(_slot(), state, 60, _context())
        assert rejection is not None
        assert rejection.rule == "resolution-not-consonant-step"

    def test_upward_motion_rejected_when_resolution_owes_downward(self) -> None:
        state = BeamState(previous_midi=59, pending_resolution=-1)
        # 60 is consonant and a step up — right size, wrong direction.
        rejection = _check(_slot(), state, 60, _context())
        assert rejection is not None
        assert rejection.rule == "resolution-must-fall"

    def test_downward_motion_rejected_when_resolution_owes_upward(self) -> None:
        state = BeamState(previous_midi=62, pending_resolution=1)
        rejection = _check(_slot(), state, 60, _context())
        assert rejection is not None
        assert rejection.rule == "resolution-must-rise"


# ---------------------------------------------------------------------------
# The six state-dependent rules (the scorer's old lines 609, 615, 624, 629,
# 659, 672). Added by QA: the arrangement tests in test_voice_leading.py only
# show that one fixture produces no violation, which cannot tell a working
# rule from a rule that never fires.
# ---------------------------------------------------------------------------

# Against melody 72, consonant interval classes are 0, 3, 4, 7, 8, 9. MIDI 62
# sits an interval class 10 below it, so it is dissonant — and D is in C major,
# so the tonality rule (which the gate checks first) stays out of the way.
DISSONANT_MIDI = 62


class TestStrongBeatDissonanceRule:
    """On a strong beat a dissonance is only legal as a held suspension."""

    def test_fresh_strong_beat_dissonance_rejected(self) -> None:
        state = BeamState(previous_midi=60)
        rejection = _check(_slot(metric_strength=0.7), state, DISSONANT_MIDI, _context())
        assert rejection is not None
        assert rejection.rule == "unprepared-strong-dissonance"

    def test_held_pitch_passes_as_a_suspension(self) -> None:
        state = BeamState(previous_midi=DISSONANT_MIDI)
        assert _check(_slot(metric_strength=0.7), state, DISSONANT_MIDI, _context()) is None


class TestWeakBeatDissonanceRule:
    """On a weak beat a dissonance needs a stepwise approach."""

    def test_stepwise_approach_passes(self) -> None:
        state = BeamState(previous_midi=60)
        assert _check(_slot(metric_strength=0.4), state, DISSONANT_MIDI, _context()) is None

    def test_leaping_approach_rejected(self) -> None:
        # A is in key and consonant, so only the leap of 5 can be the reason.
        state = BeamState(previous_midi=57)
        rejection = _check(_slot(metric_strength=0.4), state, DISSONANT_MIDI, _context())
        assert rejection is not None
        assert rejection.rule == "unapproached-weak-dissonance"

    def test_repeated_pitch_rejected(self) -> None:
        # Motion 0 is not a step, so a weak-beat dissonance cannot be restruck.
        state = BeamState(previous_midi=DISSONANT_MIDI)
        rejection = _check(_slot(metric_strength=0.4), state, DISSONANT_MIDI, _context())
        assert rejection is not None
        assert rejection.rule == "unapproached-weak-dissonance"

    def test_dissonance_without_a_previous_note_rejected(self) -> None:
        rejection = _check(_slot(metric_strength=0.4), BeamState(), DISSONANT_MIDI, _context())
        assert rejection is not None
        assert rejection.rule == "unapproached-weak-dissonance"


class TestProtectedPhraseDissonanceRule:
    """A protected phrase bars a strong-beat dissonance the suspension
    exception would otherwise allow."""

    def test_suspension_rejected_inside_a_protected_phrase(self) -> None:
        state = BeamState(previous_midi=DISSONANT_MIDI)
        slot = _slot(metric_strength=0.7, identity_policy="protected")
        rejection = _check(slot, state, DISSONANT_MIDI, _context())
        assert rejection is not None
        assert rejection.rule == "protected-strong-dissonance"

    def test_the_same_suspension_passes_in_a_free_phrase(self) -> None:
        state = BeamState(previous_midi=DISSONANT_MIDI)
        slot = _slot(metric_strength=0.7, identity_policy="free")
        assert _check(slot, state, DISSONANT_MIDI, _context()) is None


class TestLeapRule:
    """The counterline may leap an octave, not more."""

    def test_octave_leap_passes(self) -> None:
        state = BeamState(previous_midi=48)
        assert _check(_slot(), state, 60, _context()) is None

    def test_leap_over_an_octave_rejected(self) -> None:
        state = BeamState(previous_midi=47)
        rejection = _check(_slot(), state, 60, _context())
        assert rejection is not None
        assert rejection.rule == "leap-over-octave"

    def test_downward_leap_over_an_octave_rejected(self) -> None:
        state = BeamState(previous_midi=65)
        rejection = _check(_slot(), state, 52, _context())
        assert rejection is not None
        assert rejection.rule == "leap-over-octave"

    def test_first_note_has_no_leap_to_measure(self) -> None:
        # previous_midi None means motion is 0, so the cap cannot fire.
        assert _check(_slot(), BeamState(), 36, _context()) is None


class TestParallelPerfectsRule:
    """Two perfect intervals in a row, both voices moving the same way."""

    def test_parallel_fifths_rejected(self) -> None:
        # Melody 60 → 72 rises; counterline 53 → 65 rises. Both intervals are
        # interval class 7.
        state = BeamState(previous_midi=53, previous_melody=60, previous_interval_class=7)
        rejection = _check(_slot(), state, 65, _context(melody_midi=72))
        assert rejection is not None
        assert rejection.rule == "parallel-perfects"

    def test_contrary_motion_into_a_fifth_passes(self) -> None:
        # Melody 74 → 72 falls while the counterline 64 → 65 rises.
        state = BeamState(previous_midi=64, previous_melody=74, previous_interval_class=7)
        assert _check(_slot(), state, 65, _context(melody_midi=72)) is None

    def test_parallel_motion_into_a_third_passes(self) -> None:
        # Interval class 9 is consonant but not perfect, so the rule is silent.
        state = BeamState(previous_midi=52, previous_melody=60, previous_interval_class=9)
        assert _check(_slot(), state, 64, _context(melody_midi=72)) is None

    def test_rule_is_silent_on_the_first_slot(self) -> None:
        # No previous melody note means there is no motion to call parallel.
        state = BeamState(previous_midi=53, previous_melody=None, previous_interval_class=7)
        assert _check(_slot(), state, 65, _context(melody_midi=72)) is None


class TestProtectedVoiceCrossingRule:
    """In a protected phrase the counterline stays below the melody unless the
    instrument floor leaves it nowhere to go."""

    def test_crossing_above_a_protected_melody_rejected(self) -> None:
        # Melody 72 is 36 semitones above the cello floor, so staying below is
        # possible and crossing is a choice.
        slot = _slot(identity_policy="protected")
        rejection = _check(slot, BeamState(previous_midi=72), 72, _context(melody_midi=72))
        assert rejection is not None
        assert rejection.rule == "protected-voice-crossing"

    def test_crossing_allowed_when_the_melody_sits_on_the_floor(self) -> None:
        # Melody 38 leaves under 3 semitones above the cello floor of 36.
        slot = _slot(identity_policy="protected")
        context = _context(melody_midi=38, tonal_pcs=C_MAJOR_PCS)
        assert _check(slot, BeamState(previous_midi=38), 38, context) is None

    def test_crossing_allowed_in_a_free_phrase(self) -> None:
        # Scored with a penalty rather than banned — not the gate's business.
        slot = _slot(identity_policy="free")
        assert _check(slot, BeamState(previous_midi=72), 72, _context(melody_midi=72)) is None

    def test_staying_below_the_melody_passes(self) -> None:
        slot = _slot(identity_policy="protected")
        assert _check(slot, BeamState(previous_midi=60), 60, _context(melody_midi=72)) is None


class TestGateIsTotal:
    """The gate owns every hard rejection: a candidate it passes is scored."""

    def test_every_rule_id_is_distinct(self) -> None:
        from miare.counterpoint import HARD_CONSTRAINT_RULES

        assert len(HARD_CONSTRAINT_RULES) == len(set(HARD_CONSTRAINT_RULES))
        assert len(HARD_CONSTRAINT_RULES) == 11

    def test_score_action_has_no_bare_rejection_left(self) -> None:
        import inspect

        from miare.counterpoint import _score_action

        body = inspect.getsource(_score_action)
        assert "return None" not in body
