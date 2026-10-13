"""
Hard-constraint gate tests: the rules that make a candidate pitch unusable,
called directly instead of inferred from a finished arrangement.

Every rule here is hard (structurally impossible in output, seed-independent).
Preference arithmetic lives in `_score_action` and is not tested here.

The gate is pure: given a slot, a beam state, a candidate pitch and the
precomputed slot context it returns a `Rejection` or `None`. No arrangement
round trip, no analysis, no RNG.

Covered here: the five rejections that were the scorer's lines 552, 586, 598,
600 and 602 — the rest ban, the tonality ban and the three owed-resolution
rules. The six state-dependent rules (strong- and weak-beat dissonance, the
protected-phrase dissonance ban, the octave leap cap, parallel perfects and
protected voice crossing) are enforced by the same gate but still only covered
through the arrangement in `tests/test_voice_leading.py`; unit tests for them
are a follow-up card, kept out of this PR to stay under the 400-line cap.
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
# The five rules this PR covers (the scorer's old lines 552, 586, 598, 600, 602)
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
