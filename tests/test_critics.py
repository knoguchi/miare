"""
Critic tests: DESIGN.md §9.5's eight evaluators, called on one candidate pitch
instead of inferred from a finished arrangement.

Everything here is a preference, not a hard rule. A critic returns a number and
its reasons; nothing it says can keep a pitch out of the output — that is
`check_hard_constraints`, covered in `tests/test_hard_constraints.py`.

Each critic owns one key of the scorer's breakdown dict and may write no other.
`TestCriticSeam` pins that the extraction is complete — the scorer computes no
breakdown key by hand — so a half-moved critic fails a test instead of passing
quietly.

Weights come from `CRITIC_WEIGHTS`, keyed by voice relationship (DESIGN.md
§11.3). `countermelody` is all 1.0 and reproduces the coefficients this module
has always used, so the arrangement is unchanged; `TestWeights` is what proves
the weight is actually read rather than declared.
"""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from random import Random

import pytest

from miare.counterpoint import (
    CRITIC_NAMES,
    CRITIC_WEIGHTS,
    BeamState,
    CandidateContext,
    CounterpointConfig,
    CounterpointCritic,
    CounterpointSlot,
    CriticInput,
    CriticScore,
    HarmonyCritic,
    InstrumentCritic,
    LookaheadCritic,
    MelodyCritic,
    PhraseCritic,
    SlotContext,
    SourceIdentityCritic,
    SourceLanguageCritic,
    build_critics,
    critic_weights,
)
from miare.instruments import get_profile


CELLO = get_profile("cello")

C_MAJOR_PCS = frozenset({0, 2, 4, 5, 7, 9, 11})

COUNTERMELODY = critic_weights("countermelody")

CONFIG = CounterpointConfig(seed=1)


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


def _input(
    state: BeamState,
    midi: int | None,
    slot: CounterpointSlot | None = None,
    context: SlotContext | None = None,
) -> CriticInput:
    """One candidate pitch to judge — no beam, no analysis, no RNG."""
    slot = slot if slot is not None else _slot()
    context = context if context is not None else _context()
    return CriticInput(
        song=_SONG,
        profile=CELLO,
        config=CONFIG,
        slot=slot,
        state=state,
        midi=midi,
        context=context,
        candidate=CandidateContext.build(slot, state, midi, context),
    )


def _score(critic, state, midi, slot=None, context=None) -> CriticScore:
    return critic.score(_input(state, midi, slot, context))


class _Language:
    """The two `MusicalLanguage` fields the extracted critics read. None of the
    three touches the rest of it, so a stub keeps these tests off the analysis
    pipeline."""

    rhythmic_vocabulary: dict[str, int] = {}
    motif_vocabulary: list[dict] = []


class _Song:
    """Likewise for `SongIR`: only `musical_language` is reachable from here."""

    musical_language = _Language()


_SONG = _Song()


# ---------------------------------------------------------------------------
# MelodyCritic — the counterline judged as a tune of its own
# ---------------------------------------------------------------------------

class TestMelodyCritic:
    """Step beats leap, a big leap must be recovered, and no pitch or
    direction may be worn out."""

    critic = MelodyCritic(COUNTERMELODY)

    def test_stepwise_motion_is_rewarded(self) -> None:
        result = _score(self.critic, BeamState(previous_midi=60), 62)
        assert result.value == pytest.approx(1.2)
        assert "stepwise counterline motion" in result.reasons

    def test_a_leap_over_a_fifth_is_penalised(self) -> None:
        result = _score(self.critic, BeamState(previous_midi=60), 69)
        assert result.value == pytest.approx(-1.3)

    def test_recovering_a_big_leap_is_rewarded(self) -> None:
        # Previous motion was +9; stepping back down by 2 recovers it.
        state = BeamState(previous_midi=69, last_motion=9)
        result = _score(self.critic, state, 67)
        assert result.value == pytest.approx(1.2 + 1.4)
        assert "recovered previous leap" in result.reasons

    def test_continuing_after_a_big_leap_is_penalised(self) -> None:
        state = BeamState(previous_midi=69, last_motion=9)
        result = _score(self.critic, state, 71)
        assert result.value == pytest.approx(1.2 - 2.0)

    def test_the_first_note_has_no_contour_to_judge(self) -> None:
        assert _score(self.critic, BeamState(), 60).value == 0.0

    def test_a_rest_has_no_contour_to_judge(self) -> None:
        state = BeamState(previous_midi=60)
        assert _score(self.critic, state, None).value == 0.0

    def test_a_pitch_struck_a_fourth_time_is_penalised(self) -> None:
        state = _state_with_recent([60, 62, 60, 62, 60])
        result = _score(self.critic, state, 60)
        # Stepwise from 62 (+1.2), but 60 already sounded three times (-1.0).
        assert result.value == pytest.approx(1.2 - 1.0)

    def test_an_overlong_one_direction_contour_is_penalised(self) -> None:
        state = _state_with_recent([55, 57, 59, 60, 62])
        result = _score(self.critic, state, 64)
        assert "overlong one-direction contour penalty" in result.reasons
        assert result.value == pytest.approx(1.2 - 1.6)

    def test_a_turning_contour_escapes_that_penalty(self) -> None:
        state = _state_with_recent([55, 57, 59, 62, 60])
        result = _score(self.critic, state, 62)
        assert "overlong one-direction contour penalty" not in result.reasons


def _state_with_recent(midis: list[int]) -> BeamState:
    """A beam state whose recent window is `midis`, the last one as previous.

    `MelodyCritic`'s repetition stage reads the window off `state.actions`, so
    the pitches have to arrive as actions on consecutive slots.
    """
    from miare.counterpoint import CounterpointAction

    actions = [
        CounterpointAction(
            slot=_slot(index=index, start=Fraction(index)),
            midi=midi,
            source_ids=[],
            breakdown={},
            reasons=[],
        )
        for index, midi in enumerate(midis)
    ]
    return BeamState(
        actions=actions,
        previous_midi=midis[-1],
        last_motion=midis[-1] - midis[-2] if len(midis) > 1 else 0,
    )


# ---------------------------------------------------------------------------
# CounterpointCritic — the candidate against the primary melody
# ---------------------------------------------------------------------------

class TestCounterpointCritic:
    """Dissonance obligations, relative motion, spacing and crossing. A rest
    is a counterpoint choice, so it is scored here too."""

    critic = CounterpointCritic(COUNTERMELODY)

    def test_a_rest_under_an_active_melody_is_rewarded(self) -> None:
        slot = _slot(melody_activity=2)
        result = _score(self.critic, BeamState(), None, slot=slot)
        assert result.value == pytest.approx(0.8)
        assert "rests under active primary melody" in result.reasons

    def test_a_second_rest_in_a_row_is_penalised_harder(self) -> None:
        state = BeamState(rest_run=1)
        assert _score(self.critic, state, None).value == pytest.approx(-1.2)

    def test_a_first_rest_is_penalised_lightly(self) -> None:
        assert _score(self.critic, BeamState(), None).value == pytest.approx(-0.25)

    def test_resolving_a_carried_dissonance_is_rewarded(self) -> None:
        state = BeamState(previous_midi=62, pending_resolution=-1)
        result = _score(self.critic, state, 60)
        assert "resolved carried dissonance obligation" in result.reasons

    def test_a_strong_beat_suspension_is_rewarded(self) -> None:
        # 62 against melody 72 is interval class 10 — dissonant.
        slot = _slot(metric_strength=0.7)
        state = BeamState(previous_midi=62)
        result = _score(self.critic, state, 62, slot=slot)
        assert "prepared suspension; downward resolution required" in result.reasons

    def test_a_weak_beat_passing_dissonance_is_rewarded(self) -> None:
        slot = _slot(metric_strength=0.4)
        state = BeamState(previous_midi=60)
        result = _score(self.critic, state, 62, slot=slot)
        assert (
            "stepwise weak-beat dissonance; continuation required" in result.reasons
        )

    def test_contrary_motion_is_rewarded(self) -> None:
        # Melody falls 74 → 72 while the counterline rises 60 → 62.
        state = BeamState(previous_midi=60, previous_melody=74)
        result = _score(self.critic, state, 62)
        assert "contrary motion" in result.reasons

    def test_parallel_motion_is_penalised(self) -> None:
        # Melody rises 71 → 72, counterline rises 59 → 60.
        state = BeamState(previous_midi=59, previous_melody=71)
        result = _score(self.critic, state, 60)
        assert "contrary motion" not in result.reasons
        assert "oblique motion" not in result.reasons
        assert result.value < 0

    def test_oblique_motion_is_rewarded_less_than_contrary(self) -> None:
        held = BeamState(previous_midi=60, previous_melody=72)
        oblique = _score(self.critic, held, 60)
        contrary = _score(
            self.critic, BeamState(previous_midi=60, previous_melody=74), 62
        )
        assert "oblique motion" in oblique.reasons
        assert contrary.value > oblique.value

    def test_comfortable_spacing_is_rewarded(self) -> None:
        # 65 sits 7 below melody 72 — inside 3..19, and not an octave, so the
        # bare-octave penalty stays out of the number.
        result = _score(self.critic, BeamState(), 65)
        assert result.value == pytest.approx(0.7)

    def test_spacing_wider_than_two_octaves_earns_nothing(self) -> None:
        # 53 sits 19 below melody 72; 52 sits 20 below, past the reward's edge.
        # The stage is called alone: 52 is also dissonant against 72, and the
        # dissonance stage's number would hide the spacing one.
        assert self._spacing(53) == pytest.approx(0.7)
        assert self._spacing(52) == pytest.approx(0.0)

    def _spacing(self, midi: int) -> float:
        return self.critic.stage("spacing", _input(BeamState(), midi)).value

    def test_crossing_above_the_melody_is_penalised(self) -> None:
        result = _score(self.critic, BeamState(), 74)
        assert "voice-crossing penalty" in result.reasons
        assert result.value < 0

    def test_a_bare_octave_off_a_cadence_is_penalised(self) -> None:
        # 60 against melody 72 is interval class 0.
        plain = _score(self.critic, BeamState(), 60)
        cadential = _score(self.critic, BeamState(), 60, slot=_slot(cadence=True))
        assert cadential.value - plain.value == pytest.approx(1.4)

    def test_a_consonance_on_a_strong_beat_is_rewarded(self) -> None:
        strong = _score(self.critic, BeamState(), 60, slot=_slot(metric_strength=0.7))
        weak = _score(self.critic, BeamState(), 60, slot=_slot(metric_strength=0.4))
        assert strong.value - weak.value == pytest.approx(0.8)

    def test_a_slot_with_no_melody_anchor_scores_no_spacing(self) -> None:
        result = _score(self.critic, BeamState(previous_midi=60), 62,
                        context=_context(melody_midi=None))
        assert result.value == 0.0


# ---------------------------------------------------------------------------
# HarmonyCritic — the pitch against what sounds and what was inferred
# ---------------------------------------------------------------------------

class TestHarmonyCritic:
    """A pitch class already sounding in the source outranks one the harmony
    analysis only guessed at."""

    critic = HarmonyCritic(COUNTERMELODY)

    def test_a_pitch_class_the_source_sounds_is_rewarded(self) -> None:
        context = _context(source_pcs=frozenset({0}), harmony_pcs=frozenset())
        result = _score(self.critic, BeamState(), 60, context=context)
        assert result.value == pytest.approx(1.7)
        assert "pitch class supported by existing source voices" in result.reasons

    def test_a_pitch_class_the_source_contradicts_is_penalised(self) -> None:
        context = _context(source_pcs=frozenset({0}), harmony_pcs=frozenset())
        result = _score(self.critic, BeamState(), 62, context=context)
        assert result.value == pytest.approx(-1.2)

    def test_source_support_outranks_inferred_harmony(self) -> None:
        sounding = _score(
            self.critic, BeamState(), 60,
            context=_context(source_pcs=frozenset({0}), harmony_pcs=frozenset()),
        )
        inferred = _score(
            self.critic, BeamState(), 60,
            context=_context(source_pcs=frozenset(), harmony_pcs=frozenset({0})),
        )
        assert sounding.value > inferred.value

    def test_a_chord_tone_is_rewarded_when_nothing_sounds(self) -> None:
        result = _score(self.critic, BeamState(), 60)
        assert result.value == pytest.approx(0.7)

    def test_a_non_chord_tone_is_penalised_when_nothing_sounds(self) -> None:
        result = _score(self.critic, BeamState(), 62)
        assert result.value == pytest.approx(-0.25)

    def test_an_inferred_chord_tone_also_sounding_gets_both(self) -> None:
        context = _context(source_pcs=frozenset({0}), harmony_pcs=frozenset({0}))
        result = _score(self.critic, BeamState(), 60, context=context)
        assert result.value == pytest.approx(1.7 + 0.7)

    def test_a_rest_has_no_pitch_class_to_place(self) -> None:
        assert _score(self.critic, BeamState(), None).value == 0.0


# ---------------------------------------------------------------------------
# Weights by voice relationship — DESIGN.md §11.3
# ---------------------------------------------------------------------------

class TestWeights:
    """The weight set is selected by relationship and actually multiplies the
    critic's output."""

    def test_countermelody_weights_are_all_one(self) -> None:
        assert set(CRITIC_WEIGHTS["countermelody"]) == set(CRITIC_NAMES)
        assert set(CRITIC_WEIGHTS["countermelody"].values()) == {1.0}

    def test_every_weight_set_covers_all_eight_critics(self) -> None:
        for relationship, weights in CRITIC_WEIGHTS.items():
            assert set(weights) == set(CRITIC_NAMES), relationship

    def test_a_weight_scales_what_the_critic_awards(self) -> None:
        state = BeamState(previous_midi=60)
        plain = _score(MelodyCritic(COUNTERMELODY), state, 62)
        doubled = _score(
            MelodyCritic({**COUNTERMELODY, "melody": 2.0}), state, 62
        )
        assert doubled.value == pytest.approx(plain.value * 2.0)

    def test_a_weight_of_one_is_an_exact_no_op(self) -> None:
        # Not approx: the countermelody arrangement is bit-for-bit unchanged
        # only if weighting at 1.0 leaves every term's last bit alone.
        state = _state_with_recent([55, 57, 59, 60, 62])
        weighted = _score(MelodyCritic(COUNTERMELODY), state, 64)
        assert weighted.terms == [1.2, -1.6]

    def test_equal_leans_on_voice_independence(self) -> None:
        # DESIGN.md §11.3 names this: `equal` weights independence higher.
        assert CRITIC_WEIGHTS["equal"]["counterpoint"] > 1.0

    def test_support_leans_on_the_principal_melody_staying_clear(self) -> None:
        assert CRITIC_WEIGHTS["support"]["source_identity"] > 1.0

    def test_an_unknown_relationship_is_an_error_not_a_fallback(self) -> None:
        with pytest.raises(ValueError, match="no critic weights"):
            critic_weights("imitation")

    def test_the_config_default_is_countermelody(self) -> None:
        assert CounterpointConfig().relationship == "countermelody"
        assert build_critics(CounterpointConfig().relationship)["melody"].weight == 1.0


class TestRemainingFiveCritics:
    """One candidate each for the five critics extracted by #9, scored without
    running the beam search."""

    def test_phrase_rewards_a_cadence_it_lands_cleanly(self) -> None:
        state = BeamState(previous_midi=60, previous_melody=74)
        # Consonant, stepwise, on the harmonic root, at the desired register:
        # the cadential root (1.5), consonance (2.0) and step (0.8), with no
        # register drift to deduct.
        score = _score(
            PhraseCritic(COUNTERMELODY),
            state,
            60,
            slot=_slot(cadence=True),
        )
        assert score.terms == [1.5, -0.0, 2.0, 0.8]

    def test_source_language_penalises_a_duration_the_source_never_uses(
        self,
    ) -> None:
        state = BeamState(previous_midi=60, previous_melody=74)
        # The stub vocabulary is empty, so the duration is foreign: -0.55.
        score = _score(SourceLanguageCritic(COUNTERMELODY), state, 62)
        assert score.terms == [-0.55]

    def test_source_identity_keeps_the_principal_melody_separate(self) -> None:
        state = BeamState(previous_midi=60, previous_melody=74)
        # Melody at 72 against a candidate at 60 is a 12-semitone gap, inside
        # the 5..16 perceptual-separation band: 1.0 * structural_importance.
        score = _score(SourceIdentityCritic(COUNTERMELODY), state, 60)
        assert score.terms == [0.5]

    def test_instrument_prefers_the_comfortable_range(self) -> None:
        state = BeamState(previous_midi=60, previous_melody=74)
        inside = _score(InstrumentCritic(COUNTERMELODY), state, 60)
        outside = _score(InstrumentCritic(COUNTERMELODY), state, CELLO.min_midi)
        assert inside.terms == [0.7]
        assert outside.terms == [-0.8]

    def test_lookahead_rewards_a_consonance_with_the_next_melody_note(
        self,
    ) -> None:
        state = BeamState(previous_midi=60, previous_melody=74)
        # A fourth below the upcoming melody note is a consonant interval
        # class; with no RNG supplied the tie-breaking jitter stays absent.
        score = _score(
            LookaheadCritic(COUNTERMELODY),
            state,
            60,
            context=_context(future_melody=67),
        )
        assert score.terms == [0.35]


# ---------------------------------------------------------------------------
# How far the extraction has got
# ---------------------------------------------------------------------------

class TestCriticSeam:
    """All eight critics are objects; the scorer must compute none of their
    keys itself."""

    def test_build_critics_returns_all_eight(self) -> None:
        assert set(build_critics("countermelody")) == set(CRITIC_NAMES)

    def test_critic_names_match_the_design_document_order(self) -> None:
        assert CRITIC_NAMES == (
            "melody",
            "counterpoint",
            "harmony",
            "phrase",
            "source_language",
            "source_identity",
            "instrument",
            "lookahead",
        )

    def test_the_scorer_writes_no_extracted_key_by_hand(self) -> None:
        import inspect

        from miare.counterpoint import _score_action

        body = inspect.getsource(_score_action)
        for name in CRITIC_NAMES:
            assert f'breakdown["{name}"]' not in body, name

    def test_every_critic_stage_has_a_method(self) -> None:
        for critic in build_critics("countermelody").values():
            for stage in critic.stages:
                assert callable(getattr(critic, f"_{stage}"))

    def test_running_the_stages_equals_running_the_whole_critic(self) -> None:
        critic = CounterpointCritic(COUNTERMELODY)
        data = _input(
            BeamState(previous_midi=60, previous_melody=74),
            62,
            slot=_slot(metric_strength=0.7),
        )
        staged = sum(critic.stage(name, data).value for name in critic.stages)
        assert staged == pytest.approx(critic.score(data).value)


class TestScorerStillAgreesWithTheCritics:
    """`_score_action` must report exactly what the critics returned — the
    extraction moved the arithmetic, it did not copy it."""

    def test_the_breakdown_matches_the_critics_run_directly(self) -> None:
        from miare.counterpoint import _score_action

        data = _input(
            BeamState(previous_midi=60, previous_melody=74, last_motion=2),
            62,
            slot=_slot(metric_strength=0.7),
            context=_context(source_pcs=frozenset({2})),
        )
        critics = build_critics("countermelody")
        action, _ = _score_action(
            data.song, data.profile, data.config, data.slot, data.state,
            data.midi, data.context, data.candidate, Random(1), critics,
        )
        # `LookaheadCritic` draws its tie-breaking jitter from the RNG, so the
        # direct call needs an identically-seeded one to land on the same value.
        direct = replace(data, rng=Random(1))
        for name, critic in critics.items():
            assert action.breakdown[name] == pytest.approx(
                critic.score(direct).value
            ), name
