from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from math import log1p
from random import Random
from typing import Any

from .constraints import (
    CONTRARY_MOTION_BONUS,
    PERFECT_INTERVAL_CLASSES,  # noqa: F401 — re-exported; test_constraints.py asserts both modules share this set
    VOICE_CROSSING_PENALTY,
    is_contrary_motion,
    is_leap_over_octave,
    is_parallel_perfect,
    is_voice_crossing,
)
from .instruments import generated_from_source, get_profile
from .ir import (
    ArrangementIR,
    GeneratedEvent,
    InstrumentProfile,
    OutputVoice,
    Pitch,
    SongIR,
    SourceScore,
    fraction_text,
)
from .theory import measure_for_time, scale_intervals


CONSONANT_INTERVAL_CLASSES = {0, 3, 4, 7, 8, 9}


# The eight critics of DESIGN.md §9.5, in the order the scorer runs them. Each
# name is the breakdown key its critic owns; nothing else may write that key.
CRITIC_NAMES: tuple[str, ...] = (
    "melody",
    "counterpoint",
    "harmony",
    "phrase",
    "source_language",
    "source_identity",
    "instrument",
    "lookahead",
)

# DESIGN.md §11.3: the weights change with the voice relationship. One multiplier
# per critic, applied to everything that critic contributes. `countermelody` is
# all 1.0, which is what the coefficients in this module already meant, so the
# arrangement it produces is unchanged.
#
# `support` and `equal` are listed because §11.3 names them: `support` leans on
# the principal melody staying clear, `equal` on voice independence. All eight
# critics read these, so a relationship's multipliers now apply to the whole
# evaluation rather than a third of it.
CRITIC_WEIGHTS: dict[str, dict[str, float]] = {
    "countermelody": {name: 1.0 for name in CRITIC_NAMES},
    "support": {**{name: 1.0 for name in CRITIC_NAMES}, "source_identity": 1.4},
    "equal": {**{name: 1.0 for name in CRITIC_NAMES}, "counterpoint": 1.4},
}

DEFAULT_RELATIONSHIP = "countermelody"


def critic_weights(relationship: str) -> dict[str, float]:
    """The per-critic multipliers for a voice relationship.

    An unknown relationship is a programming error, not a degraded arrangement:
    silently falling back to `countermelody` weights would make a `--relationship`
    typo produce a plausible-looking score.
    """
    try:
        return CRITIC_WEIGHTS[relationship]
    except KeyError:
        raise ValueError(
            f"no critic weights for relationship {relationship!r}; "
            f"known relationships are {sorted(CRITIC_WEIGHTS)}"
        ) from None


@dataclass(frozen=True)
class CounterpointConfig:
    beam_width: int = 32
    result_candidates: int = 4
    lookback_quarters: Fraction = Fraction(8)
    lookahead_quarters: Fraction = Fraction(8)
    seed: int = 1
    relationship: str = DEFAULT_RELATIONSHIP


@dataclass(frozen=True)
class CounterpointSlot:
    index: int
    start: Fraction
    duration: Fraction
    phrase_id: str
    metric_strength: float
    melody_activity: int
    texture: str
    cadence: bool
    identity_policy: str
    structural_importance: float

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


@dataclass
class CounterpointSpan:
    phrase_id: str
    start: Fraction
    end: Fraction
    texture: str
    cadence: str
    activity_strategy: str
    identity_policy: str
    motif_signature: tuple[int, ...]


@dataclass
class CounterpointPlan:
    source_mode: str
    lookback_quarters: Fraction
    lookahead_quarters: Fraction
    spans: list[CounterpointSpan]
    slots: list[CounterpointSlot]
    existing_context_events: int
    recovery_points: list[str] = field(default_factory=list)


@dataclass
class CounterpointAction:
    slot: CounterpointSlot
    midi: int | None
    source_ids: list[str]
    breakdown: dict[str, float]
    reasons: list[str]
    dissonant: bool = False


@dataclass
class BeamState:
    actions: list[CounterpointAction] = field(default_factory=list)
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    previous_midi: int | None = None
    previous_melody: int | None = None
    previous_interval_class: int | None = None
    last_motion: int = 0
    pending_resolution: int | None = None
    rest_run: int = 0


@dataclass
class CounterpointResult:
    events: list[GeneratedEvent]
    score: float
    breakdown: dict[str, float]
    plan: CounterpointPlan
    provenance: list[dict[str, Any]]


@dataclass(frozen=True)
class SlotContext:
    melody_midi: int | None
    source_pcs: frozenset[int]
    source_midis: tuple[int, ...]
    source_ids: tuple[str, ...]
    source_bass_midi: int | None
    harmony_pcs: frozenset[int]
    harmony_root: int | None
    tonal_pcs: frozenset[int]
    future_melody: int | None
    phrase_start: Fraction
    phrase_end: Fraction
    desired_register: int
    motif_intervals: frozenset[int]


@dataclass(frozen=True)
class CandidateContext:
    """Per-candidate values derived from the slot, the beam state and the
    candidate pitch. The hard-constraint gate and the scorer both read them,
    so neither recomputes an interval or a motion the other already has.

    `interval_class` and `consonant` are None/True when the slot has no melody
    anchor: nothing sounds against the candidate, so no interval exists.
    """

    interval_class: int | None
    consonant: bool
    strong: bool
    motion: int
    spacing: int | None

    @classmethod
    def build(
        cls,
        slot: CounterpointSlot,
        state: BeamState,
        midi: int | None,
        context: SlotContext,
    ) -> CandidateContext:
        melody = context.melody_midi
        sounding = midi is not None and melody is not None
        interval_class = (melody - midi) % 12 if sounding else None
        previous = state.previous_midi
        return cls(
            interval_class=interval_class,
            consonant=(
                interval_class in CONSONANT_INTERVAL_CLASSES
                if interval_class is not None
                else True
            ),
            strong=slot.metric_strength >= 0.7,
            motion=midi - previous if midi is not None and previous is not None else 0,
            spacing=melody - midi if sounding else None,
        )


@dataclass(frozen=True)
class Rejection:
    """Why a candidate pitch may not be used at all. `rule` is one of
    HARD_CONSTRAINT_RULES.

    A rejection is not a score of -inf: DESIGN.md §3.5 keeps constraint
    violations out of the output entirely and leaves preferences to the score.
    """

    rule: str


# One id per hard rule, in the order the gate checks them. These were eleven
# bare `return None` statements inside the scorer before this gate existed.
HARD_CONSTRAINT_RULES: tuple[str, ...] = (
    "rest-owes-resolution",
    "non-tonal-pitch",
    "resolution-not-consonant-step",
    "resolution-must-fall",
    "resolution-must-rise",
    "unprepared-strong-dissonance",
    "unapproached-weak-dissonance",
    "protected-strong-dissonance",
    "leap-over-octave",
    "parallel-perfects",
    "protected-voice-crossing",
)


def check_hard_constraints(
    profile: InstrumentProfile,
    slot: CounterpointSlot,
    state: BeamState,
    midi: int | None,
    context: SlotContext,
    candidate: CandidateContext,
) -> Rejection | None:
    """Return the first rule a candidate breaks, or None when it is usable.

    Pure: no scoring, no RNG, no state mutation. DESIGN.md §9.5 runs this as
    step 3, before the critics of step 4 ever see the candidate.
    """
    if midi is None:
        # A carried dissonance must resolve onto a pitch, and a cadence slot
        # needs a sounding note; a rest cannot supply either.
        if state.pending_resolution is not None or slot.cadence:
            return Rejection("rest-owes-resolution")
        return None

    if not context.source_pcs and midi % 12 not in context.tonal_pcs:
        return Rejection("non-tonal-pitch")

    consonant = candidate.consonant
    strong = candidate.strong
    motion = candidate.motion

    if state.pending_resolution is not None:
        if state.previous_midi is None or not consonant or abs(motion) > 2:
            return Rejection("resolution-not-consonant-step")
        if state.pending_resolution < 0 and motion >= 0:
            return Rejection("resolution-must-fall")
        if state.pending_resolution > 0 and motion <= 0:
            return Rejection("resolution-must-rise")
    elif not consonant:
        if strong:
            # A held consonance becoming dissonant may act as a suspension.
            if state.previous_midi != midi:
                return Rejection("unprepared-strong-dissonance")
        else:
            if state.previous_midi is None or not 0 < abs(motion) <= 2:
                return Rejection("unapproached-weak-dissonance")

    if slot.identity_policy == "protected" and strong and not consonant:
        return Rejection("protected-strong-dissonance")

    if state.previous_midi is not None and is_leap_over_octave(motion):
        return Rejection("leap-over-octave")

    if (
        context.melody_midi is not None
        and state.previous_melody is not None
        and state.previous_midi is not None
    ):
        if is_parallel_perfect(
            previous_interval_class=state.previous_interval_class,
            interval_class=candidate.interval_class,
            melody_motion=context.melody_midi - state.previous_melody,
            voice_motion=motion,
        ):
            return Rejection("parallel-perfects")

    # Crossing above a protected melody is only allowed when the instrument
    # floor leaves the counterline nowhere below to go. `midi >= melody_midi`
    # means spacing <= 0, so the scorer's `3 <= spacing <= 19` arm cannot also
    # be true — this is the same branch it guarded.
    if (
        context.melody_midi is not None
        and slot.identity_policy == "protected"
        and is_voice_crossing(midi, context.melody_midi)
        and context.melody_midi - profile.min_midi >= 3
    ):
        return Rejection("protected-voice-crossing")

    return None


def _phrase_at(song: SongIR, time: Fraction):
    for phrase in song.phrases:
        if phrase.start <= time < phrase.end:
            return phrase
    if time < song.phrases[0].start:
        return song.phrases[0]
    return song.phrases[-1]


def _planning_melody(song: SongIR) -> list:
    return song.structural_melody or song.melody


def _melody_events_in(
    song: SongIR, start: Fraction, end: Fraction
) -> list:
    return [
        event for event in _planning_melody(song)
        if event.start < end and event.end > start and event.pitch is not None
    ]


def _melody_anchor(song: SongIR, start: Fraction, end: Fraction) -> int | None:
    events = _melody_events_in(song, start, end)
    if not events:
        return None
    return max(
        events,
        key=lambda event: (
            float(min(event.end, end) - max(event.start, start)),
            -float(event.start),
        ),
    ).pitch.midi


def _metric_strength(score: SourceScore, time: Fraction) -> float:
    measure = measure_for_time(score, time)
    beat = Fraction(4, measure.beat_type)
    position = time - measure.start
    if position == 0:
        return 1.0
    if position % beat == 0:
        return 0.75
    if position % (beat / 2) == 0:
        return 0.35
    return 0.2


def _phrase_motif_signature(song: SongIR, phrase_id: str) -> tuple[int, ...]:
    phrase = next(item for item in song.phrases if item.id == phrase_id)
    notes = [
        event.pitch.midi
        for event in _planning_melody(song)
        if phrase.start <= event.start < phrase.end and event.pitch is not None
    ]
    return tuple(
        right - left for left, right in zip(notes, notes[1:])
    )[:4]


def build_counterpoint_plan(
    source: SourceScore,
    song: SongIR,
    config: CounterpointConfig,
) -> CounterpointPlan:
    signatures = {
        phrase.id: _phrase_motif_signature(song, phrase.id)
        for phrase in song.phrases
    }
    theme_signature = signatures[song.phrases[0].id]
    signature_counts: dict[tuple[int, ...], int] = {}
    for signature in signatures.values():
        if signature:
            signature_counts[signature] = signature_counts.get(signature, 0) + 1
    identity_policies = {}
    for index, phrase in enumerate(song.phrases):
        signature = signatures[phrase.id]
        is_theme = bool(signature) and (
            signature == theme_signature
            or signature_counts.get(signature, 0) > 1
        )
        identity_policies[phrase.id] = (
            "protected" if index == 0 or is_theme else "balanced"
        )

    raw_slots: list[tuple[Fraction, Fraction, int, str]] = []
    # Long movements need a structural search grid. Fine subdivisions remain
    # useful for short phrases, but applying them to every held note in a
    # multi-hundred-bar movement makes the state space needlessly enormous.
    fine_grain = source.measures[-1].end <= 300
    for measure in source.measures:
        beat = Fraction(4, measure.beat_type)
        cursor = measure.start
        while cursor < measure.end:
            beat_end = min(cursor + beat, measure.end)
            onsets = [
                event for event in _planning_melody(song)
                if cursor <= event.start < beat_end
            ]
            sounding = _melody_events_in(song, cursor, beat_end)
            # Complement the melody: sustain under active motion and move under
            # held notes or rests.
            if len(onsets) >= 2:
                raw_slots.append((cursor, beat_end - cursor, len(onsets), "first-species-like"))
            elif fine_grain and sounding and (
                len(onsets) == 0
                or max(event.duration for event in sounding) >= beat
            ):
                half = (beat_end - cursor) / 2
                raw_slots.append((cursor, half, len(onsets), "second-species-like"))
                raw_slots.append((cursor + half, half, len(onsets), "second-species-like"))
            elif sounding:
                raw_slots.append((cursor, beat_end - cursor, len(onsets), "first-species-like"))
            else:
                raw_slots.append((cursor, beat_end - cursor, len(onsets), "mixed"))
            cursor = beat_end

    phrase_boundaries = {
        boundary
        for phrase in song.phrases
        for boundary in (phrase.start, phrase.end)
    }
    split_slots: list[tuple[Fraction, Fraction, int, str]] = []
    for start, duration, activity, texture in raw_slots:
        boundaries = sorted(
            boundary for boundary in phrase_boundaries
            if start < boundary < start + duration
        )
        points = [start, *boundaries, start + duration]
        split_slots.extend(
            (left, right - left, activity, texture)
            for left, right in zip(points, points[1:])
            if right > left
        )

    slots: list[CounterpointSlot] = []
    for index, (start, duration, activity, texture) in enumerate(split_slots):
        phrase = _phrase_at(song, start)
        identity_policy = identity_policies[phrase.id]
        cadence = start + duration == phrase.end
        importance = 0.8 if identity_policy == "protected" else 0.45
        if start == phrase.start:
            importance = max(importance, 0.9)
        if cadence:
            importance = 1.0
        slots.append(
            CounterpointSlot(
                index=index,
                start=start,
                duration=duration,
                phrase_id=phrase.id,
                metric_strength=_metric_strength(source, start),
                melody_activity=activity,
                texture=texture,
                cadence=cadence,
                identity_policy=identity_policy,
                structural_importance=importance,
            )
        )

    melody_ids = {event.id for event in song.melody}
    existing_context = [
        event
        for part in source.parts
        for event in part.events
        if event.pitch is not None and event.id not in melody_ids
    ]
    source_mode = "hybrid" if existing_context else "generated"
    spans = []
    for phrase in song.phrases:
        phrase_slots = [slot for slot in slots if slot.phrase_id == phrase.id]
        textures = [slot.texture for slot in phrase_slots]
        texture = max(set(textures), key=textures.count) if textures else "mixed"
        spans.append(
            CounterpointSpan(
                phrase_id=phrase.id,
                start=phrase.start,
                end=phrase.end,
                texture=texture,
                cadence=phrase.cadence,
                activity_strategy="complement-primary-melody",
                identity_policy=identity_policies[phrase.id],
                motif_signature=signatures[phrase.id],
            )
        )
    return CounterpointPlan(
        source_mode=source_mode,
        lookback_quarters=config.lookback_quarters,
        lookahead_quarters=config.lookahead_quarters,
        spans=spans,
        slots=slots,
        existing_context_events=len(existing_context),
    )


def _source_context(
    source: SourceScore,
    song: SongIR,
    start: Fraction,
    end: Fraction,
) -> tuple[set[int], list[int], list[str]]:
    melody_ids = {event.id for event in song.melody}
    events = [
        event
        for part in source.parts
        for event in part.events
        if (
            event.pitch is not None
            and event.id not in melody_ids
            and event.start < end
            and event.end > start
        )
    ]
    return (
        {event.pitch.midi % 12 for event in events},
        [event.pitch.midi for event in events],
        [event.id for event in events],
    )


def _harmony_context(
    song: SongIR, time: Fraction
) -> tuple[frozenset[int], int | None]:
    harmonic_slice = next(
        (
            item for item in song.harmony
            if item.start <= time < item.start + item.duration
        ),
        None,
    )
    if harmonic_slice is None:
        return frozenset(), None
    return harmonic_slice.selected.pcs, harmonic_slice.selected.root_pc


def _tonal_pitch_classes(song: SongIR) -> set[int]:
    return {
        (song.tonic_pc + interval) % 12
        for interval in scale_intervals(song.mode)
    }


def _candidate_pitches(
    profile: InstrumentProfile,
    slot: CounterpointSlot,
    state: BeamState,
    context: SlotContext,
) -> list[int | None]:
    melody_midi = context.melody_midi
    source_pcs = context.source_pcs
    harmony_pcs = context.harmony_pcs
    candidates: set[int] = set()

    if melody_midi is not None:
        for interval in (3, 4, 7, 8, 9, 12, 15):
            candidates.add(melody_midi - interval)
        for interval in (3, 4, 7):
            candidates.add(melody_midi + interval)
    for pitch_class in source_pcs | harmony_pcs:
        candidates.update(
            midi for midi in range(profile.min_midi, profile.max_midi + 1)
            if midi % 12 == pitch_class
        )
    if state.previous_midi is not None:
        candidates.update(
            state.previous_midi + interval
            for interval in (-12, -7, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 7, 12)
        )

    candidates = {
        midi for midi in candidates
        if profile.min_midi <= midi <= profile.max_midi
    }
    center = (
        state.previous_midi
        if state.previous_midi is not None
        else min(
            profile.preferred_max,
            max(
                profile.preferred_min,
                (melody_midi - 8) if melody_midi is not None else profile.preferred_min + 7,
            ),
        )
    )
    ranked = sorted(
        candidates,
        key=lambda midi: (
            abs(midi - center),
            0 if midi % 12 in source_pcs else 1,
            midi,
        ),
    )[:18]

    allow_rest = (
        not slot.cadence
        and state.pending_resolution is None
        and state.rest_run < 2
    )
    result: list[int | None] = ranked
    if allow_rest:
        result.append(None)
    return result


def _recent_pitches(
    state: BeamState,
    start: Fraction,
    lookback: Fraction,
) -> list[int]:
    cutoff = start - lookback
    recent: list[int] = []
    for action in reversed(state.actions):
        if action.slot.end <= cutoff:
            break
        if action.midi is not None:
            recent.append(action.midi)
    recent.reverse()
    return recent


def _future_melody_pitch(
    song: SongIR,
    slot: CounterpointSlot,
    lookahead: Fraction,
) -> int | None:
    future = [
        event for event in _planning_melody(song)
        if slot.end <= event.start <= slot.end + lookahead and event.pitch is not None
    ]
    return future[0].pitch.midi if future else None


def _build_slot_context(
    source: SourceScore,
    song: SongIR,
    profile: InstrumentProfile,
    slot: CounterpointSlot,
    config: CounterpointConfig,
) -> SlotContext:
    melody_midi = _melody_anchor(song, slot.start, slot.end)
    source_pcs, source_midis, source_ids = _source_context(
        source, song, slot.start, slot.end
    )
    harmony_pcs, harmony_root = _harmony_context(song, slot.start)
    phrase = _phrase_at(song, slot.start)
    phrase_duration = max(Fraction(1, 128), phrase.end - phrase.start)
    phrase_position = float((slot.start - phrase.start) / phrase_duration)
    arch = 1.0 - abs(phrase_position * 2.0 - 1.0)
    phrase_melody = [
        event.pitch.midi
        for event in _planning_melody(song)
        if phrase.start <= event.start < phrase.end and event.pitch is not None
    ]
    relative_center = (
        int(sum(phrase_melody) / len(phrase_melody)) - 7
        if phrase_melody
        else profile.preferred_min + 7
    )
    desired_register = min(
        profile.preferred_max,
        max(profile.preferred_min, relative_center + int(arch * 4)),
    )
    return SlotContext(
        melody_midi=melody_midi,
        source_pcs=frozenset(source_pcs),
        source_midis=tuple(source_midis),
        source_ids=tuple(source_ids),
        source_bass_midi=min(source_midis) if source_midis else None,
        harmony_pcs=frozenset(harmony_pcs),
        harmony_root=harmony_root,
        tonal_pcs=frozenset(_tonal_pitch_classes(song)),
        future_melody=_future_melody_pitch(
            song, slot, config.lookahead_quarters
        ),
        phrase_start=phrase.start,
        phrase_end=phrase.end,
        desired_register=desired_register,
        motif_intervals=frozenset(
            abs(interval)
            for motif in song.musical_language.motif_vocabulary
            for interval in motif.get("intervals", [])
            if interval
        ),
    )



@dataclass(frozen=True)
class CriticInput:
    """Everything a critic may look at to judge one candidate pitch.

    One object instead of nine arguments: the critics read overlapping subsets
    of it, and threading nine positional parameters through every stage made
    the signature the longest thing in each method.

    `midi is None` is a rest. A rest is a real candidate — `CounterpointCritic`
    scores it — so critics check for it rather than assuming a pitch.

    `rng` is only read by `LookaheadCritic`, for the tie-breaking jitter that
    makes an otherwise-tied beam deterministic under a seed. It is optional so
    a unit test can build a `CriticInput` for any other critic without one.
    """

    song: SongIR
    profile: InstrumentProfile
    config: CounterpointConfig
    slot: CounterpointSlot
    state: BeamState
    midi: int | None
    context: SlotContext
    candidate: CandidateContext
    rng: Random | None = None


@dataclass
class CriticScore:
    """One critic's verdict on one candidate: the terms it awarded for its own
    breakdown key, plus the reasons that earned them.

    A critic never touches another critic's key and never sees the shared
    breakdown dict, so a unit test can call it on a hand-built slot and read
    the arithmetic straight off the result.

    `terms` stays an ordered list rather than a running total because float
    addition is not associative: summing a critic's terms and then adding the
    subtotal to the bucket moves the bucket's last bits, and the beam search
    sorts on those sums. The scorer adds the terms to the bucket one at a time,
    in the order they were awarded — bit-for-bit what the inline block did.
    """

    terms: list[float] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def value(self) -> float:
        """The contribution as one number, for callers that do not accumulate
        into a shared bucket."""
        total = 0.0
        for term in self.terms:
            total += term
        return total

    def add(self, value: float, reason: str | None = None) -> None:
        self.terms.append(value)
        if reason is not None:
            self.reasons.append(reason)

    def extend(self, other: CriticScore) -> None:
        self.terms.extend(other.terms)
        self.reasons.extend(other.reasons)


class Critic:
    """One of the eight evaluators of DESIGN.md §9.5.

    `name` is the breakdown key the critic owns; it writes no other. `weight`
    is its multiplier for the current voice relationship (DESIGN.md §11.3),
    applied to each term as it is awarded, so `countermelody`'s 1.0 is an exact
    no-op and the arrangement it produces is bit-for-bit unchanged.

    Subclasses list their `stages` — the ordered pieces of their arithmetic,
    each a `_<stage>` method. The scorer runs stages one at a time, which keeps
    all eight critics' reasons in the order they were written in; `score()`
    runs the whole critic at once, which is what a unit test wants.
    """

    name: str
    stages: tuple[str, ...]

    def __init__(self, weights: dict[str, float]) -> None:
        self.weight = weights[self.name]

    def stage(self, name: str, data: CriticInput) -> CriticScore:
        """One named stage's weighted contribution."""
        result = CriticScore()
        getattr(self, f"_{name}")(result, data)
        if self.weight != 1.0:
            result.terms = [term * self.weight for term in result.terms]
        return result

    def score(self, data: CriticInput) -> CriticScore:
        """Every stage in order — the critic's whole verdict on a candidate."""
        total = CriticScore()
        for name in self.stages:
            total.extend(self.stage(name, data))
        return total


class MelodyCritic(Critic):
    """Is the counterline a good tune in its own right? Step over leap, every
    big leap recovered, no pitch worn out and no contour that climbs forever.

    Reads only the candidate's own line. How it sits against the primary
    melody is `CounterpointCritic`'s business.
    """

    name = "melody"
    stages = ("contour", "repetition")

    def _contour(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None or data.state.previous_midi is None:
            return
        motion = data.candidate.motion
        leap = abs(motion)
        if leap <= 2:
            score.add(1.2, "stepwise counterline motion")
        elif leap <= 5:
            score.add(0.7)
        elif leap > 7:
            score.add(-1.3)
        if abs(data.state.last_motion) > 7:
            if motion * data.state.last_motion < 0 and abs(motion) <= 4:
                score.add(1.4, "recovered previous leap")
            else:
                score.add(-2.0)

    def _repetition(self, score: CriticScore, data: CriticInput) -> None:
        midi = data.midi
        if midi is None:
            return
        recent = _recent_pitches(
            data.state, data.slot.start, data.config.lookback_quarters
        )
        if recent.count(midi) >= 3:
            score.add(-1.0)
        if recent and max(recent + [midi]) - min(recent + [midi]) > 19:
            score.add(-0.6)
        if len(recent) >= 4:
            recent_motions = [
                right - left for left, right in zip(recent[-4:], recent[-3:])
            ]
            projected = [*recent_motions, midi - recent[-1]]
            nonzero_directions = [1 if item > 0 else -1 for item in projected if item]
            if (
                len(nonzero_directions) >= 4
                and len(set(nonzero_directions[-4:])) == 1
            ):
                score.add(-1.6, "overlong one-direction contour penalty")


class CounterpointCritic(Critic):
    """How the candidate behaves against the primary melody: dissonance
    obligations, relative motion, spacing and voice crossing.

    A rest is scored here too — silence under an active melody is a
    counterpoint choice, not the absence of one.
    """

    name = "counterpoint"
    stages = ("rest", "dissonance", "relative_motion", "spacing")

    def _rest(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is not None:
            return
        if data.slot.melody_activity >= 2:
            score.add(0.8, "rests under active primary melody")
        elif data.state.rest_run:
            score.add(-1.2)
        else:
            score.add(-0.25)

    def _dissonance(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None:
            return
        if data.state.pending_resolution is not None:
            score.add(2.0, "resolved carried dissonance obligation")
        elif not data.candidate.consonant:
            if data.candidate.strong:
                # A held consonance becoming dissonant acts as a suspension.
                score.add(0.5, "prepared suspension; downward resolution required")
            else:
                score.add(0.4, "stepwise weak-beat dissonance; continuation required")

    def _relative_motion(self, score: CriticScore, data: CriticInput) -> None:
        state = data.state
        if (
            data.midi is None
            or data.context.melody_midi is None
            or state.previous_melody is None
            or state.previous_midi is None
        ):
            return
        melody_motion = data.context.melody_midi - state.previous_melody
        motion = data.candidate.motion
        if is_contrary_motion(melody_motion, motion):
            score.add(CONTRARY_MOTION_BONUS, "contrary motion")
        elif melody_motion == 0 or motion == 0:
            score.add(0.45, "oblique motion")
        elif melody_motion * motion > 0:
            score.add(-0.2)

    def _spacing(self, score: CriticScore, data: CriticInput) -> None:
        midi, melody = data.midi, data.context.melody_midi
        if midi is None or melody is None:
            return
        spacing = data.candidate.spacing
        if 3 <= spacing <= 19:
            score.add(0.7)
        elif is_voice_crossing(midi, melody):
            score.add(VOICE_CROSSING_PENALTY, "voice-crossing penalty")
        if data.candidate.interval_class == 0 and not data.slot.cadence:
            score.add(-1.4)
        if data.candidate.strong and data.candidate.consonant:
            score.add(0.8)


class HarmonyCritic(Critic):
    """Does the pitch fit what is actually sounding and what the harmony
    analysis inferred? Existing source voices outrank the inferred chord: they
    are evidence, the chord is a guess.
    """

    name = "harmony"
    stages = ("pitch_class_support",)

    def _pitch_class_support(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None:
            return
        pitch_class = data.midi % 12
        source_pcs = data.context.source_pcs
        if pitch_class in source_pcs:
            score.add(1.7, "pitch class supported by existing source voices")
        elif source_pcs:
            score.add(-1.2)
        if pitch_class in data.context.harmony_pcs:
            score.add(0.7)
        elif not source_pcs:
            score.add(-0.25)


class PhraseCritic(Critic):
    """Does the candidate land the phrase? Register drift across the phrase,
    and the cadence itself — consonance, a stepwise arrival, the harmonic root.

    Register is a phrase-level concern rather than a melodic one: a line can be
    shapely on its own terms and still sit in the wrong octave for the phrase.
    """

    name = "phrase"
    stages = ("cadential_root", "register", "cadence")

    def _cadential_root(self, score: CriticScore, data: CriticInput) -> None:
        midi = data.midi
        if midi is None:
            return
        harmony_root = data.context.harmony_root
        if harmony_root is not None and data.slot.cadence and midi % 12 == harmony_root:
            score.add(1.5, "cadential harmonic root")

    def _register(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None:
            return
        score.add(-abs(data.midi - data.context.desired_register) * 0.035)

    def _cadence(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None or not data.slot.cadence:
            return
        if data.candidate.consonant:
            score.add(2.0, "consonant phrase ending")
        if abs(data.candidate.motion) <= 2:
            score.add(0.8, "stepwise cadential arrival")


class SourceLanguageCritic(Critic):
    """Does the candidate speak the source's own vocabulary? Its duration
    against the analysed rhythmic vocabulary, and its distance from the
    register the source voices actually occupy.

    This is the grammar of the piece rather than its themes; the specific
    thematic material is `SourceIdentityCritic`'s business.
    """

    name = "source_language"
    stages = ("rhythmic_vocabulary", "source_register")

    def _rhythmic_vocabulary(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None:
            return
        duration_key = fraction_text(data.slot.duration)
        rhythm_count = data.song.musical_language.rhythmic_vocabulary.get(
            duration_key, 0
        )
        if rhythm_count:
            score.add(
                min(1.0, log1p(rhythm_count) / 5.0),
                "duration occurs in source rhythmic vocabulary",
            )
        else:
            score.add(-0.55)

    def _source_register(self, score: CriticScore, data: CriticInput) -> None:
        midi = data.midi
        if midi is None:
            return
        source_midis = data.context.source_midis
        if not source_midis:
            return
        distance_to_source_register = min(
            abs(midi - source_midi) for source_midi in source_midis
        )
        score.add(-distance_to_source_register * 0.025)


class SourceIdentityCritic(Critic):
    """Does the arrangement protect the source's own identity — the principal
    melody staying audible, protected thematic material left room, the source's
    bass and motif intervals preserved?

    The densest of the eight, and the one DESIGN.md §11.3 re-weights most: a
    `support` voice leans on this, an `equal` voice trades it for independence.
    """

    name = "source_identity"
    stages = (
        "reserved_space",
        "phrase_entry",
        "separation",
        "pitch_class",
        "cadential_bass",
        "cadence",
        "motif_interval",
    )

    def _reserved_space(self, score: CriticScore, data: CriticInput) -> None:
        """Rest path only: silence that leaves protected material in the clear."""
        if data.midi is not None:
            return
        if data.slot.identity_policy == "protected":
            score.add(
                1.4 * data.slot.structural_importance,
                "space reserved for protected thematic material",
            )

    def _phrase_entry(self, score: CriticScore, data: CriticInput) -> None:
        """Rest path only: an unaccompanied entry at the top of a phrase."""
        if data.midi is not None:
            return
        if data.slot.start == data.context.phrase_start:
            score.add(0.9, "unaccompanied phrase entry")

    def _separation(self, score: CriticScore, data: CriticInput) -> None:
        midi, melody_midi = data.midi, data.context.melody_midi
        if midi is None or melody_midi is None:
            return
        slot = data.slot
        spacing = data.candidate.spacing
        if not 3 <= spacing <= 19 and midi >= melody_midi:
            # The gate already rejected a crossing a protected phrase could
            # have avoided, so any crossing left here is forced by the floor.
            score.add(-1.2, "crossing unavoidable near instrument floor")
        if 5 <= spacing <= 16:
            score.add(
                1.0 * slot.structural_importance,
                "principal melody kept perceptually separate",
            )
        if slot.melody_activity >= 2 and slot.identity_policy == "protected":
            score.add(-0.9)

    def _pitch_class(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None:
            return
        if data.midi % 12 in data.context.source_pcs:
            score.add(0.8 * data.slot.structural_importance)

    def _cadential_bass(self, score: CriticScore, data: CriticInput) -> None:
        midi = data.midi
        if midi is None:
            return
        source_bass_midi = data.context.source_bass_midi
        if (
            data.slot.cadence
            and source_bass_midi is not None
            and midi % 12 == source_bass_midi % 12
        ):
            score.add(1.6, "preserved source bass class at cadence")

    def _cadence(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None or not data.slot.cadence:
            return
        score.add(0.6 * data.slot.structural_importance)

    def _motif_interval(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None:
            return
        motion = data.candidate.motion
        if motion and abs(motion) in data.context.motif_intervals:
            score.add(0.35, "motion drawn from a recurring source motif interval")


class InstrumentCritic(Critic):
    """Is the pitch comfortable on the target instrument? Inside the preferred
    range rather than merely inside the playable one.

    The playable limits are a hard constraint the gate enforces, so by the time
    a candidate is scored the only question left is how idiomatic it is.
    """

    name = "instrument"
    stages = ("preferred_range",)

    def _preferred_range(self, score: CriticScore, data: CriticInput) -> None:
        midi = data.midi
        if midi is None:
            return
        profile = data.profile
        if profile.preferred_min <= midi <= profile.preferred_max:
            score.add(0.7)
        else:
            score.add(-0.8)


class LookaheadCritic(Critic):
    """Does the candidate leave the next primary-melody event somewhere good,
    and which way does a tie break?

    The jitter is the beam's tie-breaker. It is drawn from the seeded `rng` on
    every scored pitch, so it must be awarded once per candidate and in this
    position: consuming the stream anywhere else reorders a tied beam and
    changes the arrangement even though no coefficient moved.
    """

    name = "lookahead"
    stages = ("future_melody", "jitter")

    def _future_melody(self, score: CriticScore, data: CriticInput) -> None:
        midi = data.midi
        if midi is None:
            return
        future_melody = data.context.future_melody
        if future_melody is not None:
            future_interval = (future_melody - midi) % 12
            if future_interval in CONSONANT_INTERVAL_CLASSES:
                score.add(0.35, "compatible with upcoming primary-melody context")

    def _jitter(self, score: CriticScore, data: CriticInput) -> None:
        if data.midi is None or data.rng is None:
            return
        score.add(data.rng.random() * 1e-7)


def build_critics(relationship: str) -> dict[str, Critic]:
    """All eight critics of DESIGN.md §9.5 for a voice relationship, keyed by
    breakdown key."""
    weights = critic_weights(relationship)
    return {
        critic.name: critic
        for critic in (
            MelodyCritic(weights),
            CounterpointCritic(weights),
            HarmonyCritic(weights),
            PhraseCritic(weights),
            SourceLanguageCritic(weights),
            SourceIdentityCritic(weights),
            InstrumentCritic(weights),
            LookaheadCritic(weights),
        )
    }


def _score_action(
    song: SongIR,
    profile: InstrumentProfile,
    config: CounterpointConfig,
    slot: CounterpointSlot,
    state: BeamState,
    midi: int | None,
    context: SlotContext,
    candidate: CandidateContext,
    rng: Random,
    critics: dict[str, Critic] | None = None,
) -> tuple[CounterpointAction, BeamState]:
    """Score a candidate the hard-constraint gate has already passed.

    Preference arithmetic only. Callers run `check_hard_constraints` first;
    this function never rejects, so every candidate reaching it is playable.

    All eight of DESIGN.md §9.5's critics are objects; this function only
    sequences them and threads the beam state. Each stage runs where its
    arithmetic used to sit inline, so every critic's reasons stay in the order
    they were written in and each bucket's terms accumulate in the same order.
    """
    if critics is None:
        critics = build_critics(config.relationship)
    breakdown = {name: 0.0 for name in CRITIC_NAMES}
    reasons: list[str] = []
    melody_midi = context.melody_midi
    source_ids = list(context.source_ids)

    data = CriticInput(
        song=song,
        profile=profile,
        config=config,
        slot=slot,
        state=state,
        midi=midi,
        context=context,
        candidate=candidate,
        rng=rng,
    )

    def run(critic_name: str, stage_name: str) -> None:
        result = critics[critic_name].stage(stage_name, data)
        for term in result.terms:
            breakdown[critic_name] += term
        reasons.extend(result.reasons)

    if midi is None:
        run("counterpoint", "rest")
        run("source_identity", "reserved_space")
        run("source_identity", "phrase_entry")
        action = CounterpointAction(slot, None, [], breakdown, reasons)
        new_breakdown = state.breakdown.copy()
        for key, value in breakdown.items():
            new_breakdown[key] = new_breakdown.get(key, 0.0) + value
        return action, BeamState(
            actions=[*state.actions, action],
            score=state.score + sum(breakdown.values()),
            breakdown=new_breakdown,
            previous_midi=state.previous_midi,
            previous_melody=state.previous_melody,
            previous_interval_class=state.previous_interval_class,
            last_motion=state.last_motion,
            pending_resolution=state.pending_resolution,
            rest_run=state.rest_run + 1,
        )

    interval_class = candidate.interval_class
    consonant = candidate.consonant
    strong = candidate.strong
    motion = candidate.motion

    pending_resolution: int | None = None
    if state.pending_resolution is None and not consonant:
        # The obligation the dissonance creates is beam state, not a score, so
        # it stays here while `CounterpointCritic` scores the dissonance.
        pending_resolution = -1 if strong else (1 if motion > 0 else -1)
    run("counterpoint", "dissonance")

    run("melody", "contour")
    run("counterpoint", "relative_motion")

    if melody_midi is not None:
        run("counterpoint", "spacing")
        run("source_identity", "separation")

    run("harmony", "pitch_class_support")
    run("source_identity", "pitch_class")
    run("phrase", "cadential_root")
    run("source_identity", "cadential_bass")

    run("phrase", "register")
    run("phrase", "cadence")
    run("source_identity", "cadence")

    run("source_language", "rhythmic_vocabulary")
    run("source_language", "source_register")
    run("source_identity", "motif_interval")

    run("instrument", "preferred_range")

    run("melody", "repetition")

    run("lookahead", "future_melody")
    run("lookahead", "jitter")

    action = CounterpointAction(
        slot=slot,
        midi=midi,
        source_ids=source_ids,
        breakdown=breakdown,
        reasons=list(dict.fromkeys(reasons)),
        dissonant=not consonant,
    )
    new_breakdown = state.breakdown.copy()
    for key, value in breakdown.items():
        new_breakdown[key] = new_breakdown.get(key, 0.0) + value
    return action, BeamState(
        actions=[*state.actions, action],
        score=state.score + sum(breakdown.values()),
        breakdown=new_breakdown,
        previous_midi=midi,
        previous_melody=melody_midi if melody_midi is not None else state.previous_melody,
        previous_interval_class=interval_class,
        last_motion=motion,
        pending_resolution=pending_resolution,
        rest_run=0,
    )


def _deduplicate_beam(states: list[BeamState], width: int) -> list[BeamState]:
    best: dict[tuple[Any, ...], BeamState] = {}
    for state in sorted(states, key=lambda item: item.score, reverse=True):
        recent = tuple(
            action.midi for action in state.actions[-8:]
        )
        signature = (
            state.previous_midi,
            state.previous_interval_class,
            state.pending_resolution,
            state.rest_run,
            recent,
        )
        if signature not in best:
            best[signature] = state
        if len(best) >= width:
            break
    return list(best.values())


def _actions_to_events(
    actions: list[CounterpointAction],
    *,
    prefer_flats: bool,
) -> tuple[list[GeneratedEvent], list[dict[str, Any]]]:
    events: list[GeneratedEvent] = []
    provenance: list[dict[str, Any]] = []
    for action in actions:
        if action.midi is None:
            continue
        rationale = [
            *action.reasons,
            "critic scores: "
            + ", ".join(
                f"{key}={value:.2f}" for key, value in action.breakdown.items()
                if abs(value) > 1e-9
            ),
        ]
        if (
            events
            and events[-1].pitch.midi == action.midi
            and events[-1].end == action.slot.start
        ):
            events[-1].duration += action.slot.duration
            events[-1].source_ids = list(
                dict.fromkeys([*events[-1].source_ids, *action.source_ids])
            )
            events[-1].rationale = list(
                dict.fromkeys([*events[-1].rationale, *rationale])
            )
            continue
        event = GeneratedEvent(
            id=f"counterpoint-{len(events) + 1}",
            start=action.slot.start,
            duration=action.slot.duration,
            pitch=Pitch.from_midi(action.midi, prefer_flats=prefer_flats),
            role="countermelody",
            source_ids=action.source_ids,
            rationale=rationale,
        )
        events.append(event)
    for event in events:
        provenance.append(
            {
                "event_id": event.id,
                "kind": "beam-searched-counterpoint",
                "source_event_ids": event.source_ids,
                "rationale": event.rationale,
            }
        )
    return events, provenance


def generate_counterpoint_candidates(
    source: SourceScore,
    song: SongIR,
    profile: InstrumentProfile,
    config: CounterpointConfig,
) -> list[CounterpointResult]:
    if not song.phrases:
        raise ValueError("counterpoint generation requires at least one phrase")
    plan = build_counterpoint_plan(source, song, config)
    rng = Random(config.seed)
    critics = build_critics(config.relationship)
    beam = [BeamState()]
    contexts = {
        slot.index: _build_slot_context(source, song, profile, slot, config)
        for slot in plan.slots
    }

    for slot in plan.slots:
        context = contexts[slot.index]

        def expand(states: list[BeamState]) -> list[BeamState]:
            expanded_states: list[BeamState] = []
            for state in states:
                candidates = _candidate_pitches(profile, slot, state, context)
                for midi in candidates:
                    candidate = CandidateContext.build(
                        slot, state, midi, context
                    )
                    rejection = check_hard_constraints(
                        profile, slot, state, midi, context, candidate
                    )
                    if rejection is not None:
                        continue
                    _, new_state = _score_action(
                        song,
                        profile,
                        config,
                        slot,
                        state,
                        midi,
                        context,
                        candidate,
                        rng,
                        critics,
                    )
                    expanded_states.append(new_state)
            return expanded_states

        expanded = expand(beam)
        if not expanded:
            recovered_states = [
                replace(
                    state,
                    pending_resolution=None,
                    previous_interval_class=None,
                    last_motion=0,
                )
                for state in beam
            ]
            expanded = expand(recovered_states)
            if expanded:
                plan.recovery_points.append(
                    f"{slot.start}: reset unresolved counterpoint obligation"
                )
        if not expanded:
            recovered_states = [
                replace(
                    state,
                    pending_resolution=None,
                    previous_midi=None,
                    previous_melody=None,
                    previous_interval_class=None,
                    last_motion=0,
                )
                for state in beam
            ]
            expanded = expand(recovered_states)
            if expanded:
                plan.recovery_points.append(
                    f"{slot.start}: reset local voice-leading context after search exhaustion"
                )
        if not expanded:
            raise ValueError(
                f"counterpoint search exhausted at {slot.start}; "
                "no action satisfies the current obligations"
            )
        beam = _deduplicate_beam(expanded, config.beam_width)

    finished = [
        state for state in beam if state.pending_resolution is None
    ] or beam
    results: list[CounterpointResult] = []
    for state in sorted(finished, key=lambda item: item.score, reverse=True)[
        : config.result_candidates
    ]:
        events, provenance = _actions_to_events(
            state.actions, prefer_flats=source.fifths < 0
        )
        results.append(
            CounterpointResult(
                events=events,
                score=state.score,
                breakdown=state.breakdown,
                plan=plan,
                provenance=provenance,
            )
        )
    return results


def arrange_countermelody(
    source: SourceScore,
    song: SongIR,
    *,
    voice1_profile: str = "violin",
    voice2_profile: str = "violin",
    beam_width: int = 32,
    context_measures: int = 2,
    seed: int = 1,
) -> ArrangementIR:
    profile1 = get_profile(voice1_profile)
    profile2 = get_profile(voice2_profile)
    average_measure = sum(
        (measure.duration for measure in source.measures), Fraction(0)
    ) / len(source.measures)
    context = average_measure * context_measures
    config = CounterpointConfig(
        beam_width=beam_width,
        lookback_quarters=context,
        lookahead_quarters=context,
        seed=seed,
        relationship="countermelody",
    )
    melody_events, melody_diagnostics = generated_from_source(
        song.melody,
        profile_min=profile1.min_midi,
        profile_max=profile1.max_midi,
        prefer_flats=source.fifths < 0,
        phrase_boundaries={
            boundary for phrase in song.phrases
            for boundary in (phrase.start, phrase.end)
        },
    )
    realized_pitches = {
        event.source_ids[0]: event.pitch
        for event in melody_events
        if event.source_ids
    }
    realized_song = replace(
        song,
        melody=[
            replace(event, pitch=realized_pitches.get(event.id, event.pitch))
            for event in song.melody
        ],
        structural_melody=[
            replace(event, pitch=realized_pitches.get(event.id, event.pitch))
            for event in song.structural_melody
        ],
    )
    candidates = generate_counterpoint_candidates(
        source, realized_song, profile2, config
    )
    best = candidates[0]
    candidate_summary = [
        {
            "rank": index,
            "score": candidate.score,
            "critic_breakdown": candidate.breakdown,
            "event_count": len(candidate.events),
        }
        for index, candidate in enumerate(candidates, 1)
    ]
    return ArrangementIR(
        voices=[
            OutputVoice(id="voice1", profile=profile1, events=melody_events),
            OutputVoice(id="voice2", profile=profile2, events=best.events),
        ],
        relationship="countermelody",
        diagnostics=[
            *melody_diagnostics,
            f"counterpoint beam width={beam_width}, context={context} quarters",
            f"selected candidate score={best.score:.3f}",
        ],
        provenance=[
            {
                "kind": "counterpoint-plan",
                "plan": asdict(best.plan),
                "candidate_summary": candidate_summary,
                "selected_critic_breakdown": best.breakdown,
            },
            *[
                {
                    "event_id": event.id,
                    "kind": "source-melody",
                    "source_event_ids": event.source_ids,
                    "rationale": event.rationale,
                }
                for event in melody_events
            ],
            *best.provenance,
        ],
    )
