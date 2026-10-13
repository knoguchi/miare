from __future__ import annotations

from fractions import Fraction
from random import Random

from .instruments import get_profile
from .ir import (
    ArrangementIR,
    GeneratedEvent,
    HarmonicSlice,
    InstrumentProfile,
    OutputVoice,
    Pitch,
    SongIR,
    SourceScore,
)
from .musicxml import generated_from_source


def _chord_pitch_classes(harmonic_slice: HarmonicSlice) -> set[int]:
    root = harmonic_slice.selected.root_pc
    quality = harmonic_slice.selected.quality
    third = 4 if quality == "major" else 3
    fifth = 6 if quality == "diminished" else 7
    return {root, (root + third) % 12, (root + fifth) % 12}


def _melody_anchor(song: SongIR, start: Fraction, duration: Fraction) -> int | None:
    end = start + duration
    notes = [
        event for event in song.melody
        if event.start < end and event.end > start and event.pitch is not None
    ]
    if not notes:
        return None
    return max(
        notes,
        key=lambda event: (
            float(min(event.end, end) - max(event.start, start)),
            -event.start,
        ),
    ).pitch.midi


def _is_phrase_end(song: SongIR, end: Fraction) -> bool:
    return any(phrase.end == end for phrase in song.phrases)


def _candidate_midis(
    harmonic_slice: HarmonicSlice,
    profile: InstrumentProfile,
) -> list[int]:
    pitch_classes = _chord_pitch_classes(harmonic_slice)
    return [
        midi for midi in range(profile.min_midi, profile.max_midi + 1)
        if midi % 12 in pitch_classes
    ]


def generate_support_voice(
    song: SongIR,
    profile: InstrumentProfile,
    *,
    seed: int = 1,
    prefer_flats: bool = False,
) -> tuple[list[GeneratedEvent], list[dict]]:
    rng = Random(seed)
    melody_start = min(event.start for event in song.melody)
    melody_end = max(event.end for event in song.melody)
    common_duration = Fraction(1)
    if song.musical_language.rhythmic_vocabulary:
        duration_text = max(
            song.musical_language.rhythmic_vocabulary,
            key=song.musical_language.rhythmic_vocabulary.get,
        )
        common_duration = Fraction(duration_text)

    # Collect all spans up front for Viterbi DP
    all_spans: list[dict] = []
    for harmonic_index, harmonic_slice in enumerate(song.harmony, 1):
        start = harmonic_slice.start
        end = start + harmonic_slice.duration
        if end <= melody_start or start >= melody_end:
            continue
        melody_onsets = [e for e in song.melody if start <= e.start < end]
        sub_spans = [(start, harmonic_slice.duration)]
        borrowed_rhythm = False
        if (
            common_duration > 0
            and harmonic_slice.duration / common_duration in {2, 3, 4}
        ):
            # Always subdivide into common-duration steps so the support voice
            # moves rhythmically regardless of melody density.  Holding a single
            # chord tone for a half-measure while the melody is active produces
            # a static "pad" sound; quarter-note motion gives harmonic definition.
            sub_spans = [
                (start + common_duration * offset, common_duration)
                for offset in range(int(harmonic_slice.duration / common_duration))
            ]
            borrowed_rhythm = True
        n_sub = len(sub_spans)
        for sub_index, (event_start, event_duration) in enumerate(sub_spans, 1):
            candidates = _candidate_midis(harmonic_slice, profile)
            if not candidates:
                raise ValueError(f"no {profile.id} pitch available for {harmonic_slice.id}")
            epsilon = Fraction(1, 1024)
            boundary = event_start - epsilon
            boundary_melody = next(
                (
                    ev.pitch.midi for ev in song.melody
                    if ev.start <= boundary < ev.end and ev.pitch is not None
                ),
                None,
            )
            all_spans.append({
                "harmonic_index": harmonic_index,
                "sub_index": sub_index,
                "is_sub": n_sub > 1,
                "start": event_start,
                "duration": event_duration,
                "harmonic_slice": harmonic_slice,
                "candidates": candidates,
                "melody_midi": _melody_anchor(song, event_start, event_duration),
                "boundary_melody_midi": boundary_melody,
                "borrowed_rhythm": borrowed_rhythm,
            })

    if not all_spans:
        return [], []

    # Emission score (per-span, independent of previous state)
    def _emit(midi: int, span: dict) -> tuple[float, list[str]]:
        reasons: list[str] = []
        value = 0.0
        if profile.preferred_min <= midi <= profile.preferred_max:
            value += 3.0
            reasons.append("preferred register")
        else:
            value -= 2.0
        melody_midi = span["melody_midi"]
        if melody_midi is not None:
            if midi >= melody_midi:
                value -= 5.0
            spacing = melody_midi - midi
            if 5 <= spacing <= 19:
                value += 1.0
                reasons.append("clear melodic/support register")
        if midi % 12 == span["harmonic_slice"].selected.root_pc:
            value += 1.0
            reasons.append("harmonic root")
            if _is_phrase_end(song, span["start"] + span["duration"]):
                value += 2.5
                reasons.append("cadential root")
        value += rng.random() * 1e-6
        return value, reasons

    # Transition score (between consecutive spans)
    def _transit(
        midi: int,
        prev_midi: int | None,
        melody_midi: int | None,
        prev_melody: int | None,
        prev_ic: int | None,
        is_sub: bool,
    ) -> tuple[float, list[str], int | None]:
        if prev_midi is None:
            ic = (melody_midi - midi) % 12 if melody_midi is not None else None
            return 0.0, [], ic
        reasons: list[str] = []
        value = 0.0
        leap = abs(midi - prev_midi)
        value -= leap * 0.28
        if leap <= 5:
            value += 1.4
            reasons.append("smooth support-line motion")
        elif leap > 12:
            value -= 4.0
        if midi == prev_midi and is_sub:
            value -= 2.5
        if melody_midi is not None and prev_melody is not None:
            melody_dir = melody_midi - prev_melody
            support_dir = midi - prev_midi
            if melody_dir * support_dir < 0:
                value += 1.2
                reasons.append("contrary motion")
            elif melody_dir * support_dir > 0:
                value -= 0.25
            ic = (melody_midi - midi) % 12
            if prev_ic in {0, 7} and ic in {0, 7} and melody_dir * (midi - prev_midi) > 0:
                return float("-inf"), ["parallel perfect — hard banned"], ic
        else:
            ic = (melody_midi - midi) % 12 if melody_midi is not None else None
        return value, reasons, ic

    # Forward DP: dp[i][midi] = (cumulative_score, prev_midi)
    NEG_INF = float("-inf")
    dp: list[dict[int, tuple[float, int | None]]] = [{} for _ in all_spans]

    first = all_spans[0]
    for midi in first["candidates"]:
        emit_score, _ = _emit(midi, first)
        dp[0][midi] = (emit_score, None)

    for i in range(1, len(all_spans)):
        span = all_spans[i]
        prev_span = all_spans[i - 1]
        for midi in span["candidates"]:
            emit_score, _ = _emit(midi, span)
            best_score = NEG_INF
            best_prev: int | None = None
            for prev_midi, (prev_cum, _) in dp[i - 1].items():
                # boundary_melody_midi: the melody note sounding just before
                # this span's support note attacks — used for the parallel-fifth
                # check so that the "previous" interval reflects the actual note
                # at the transition boundary, not the span representative anchor.
                prev_melody = span["boundary_melody_midi"] or prev_span["melody_midi"]
                prev_ic = (
                    (prev_melody - prev_midi) % 12 if prev_melody is not None else None
                )
                trans, _, _ = _transit(
                    midi, prev_midi,
                    span["melody_midi"], prev_melody,
                    prev_ic, span["is_sub"],
                )
                total = prev_cum + trans + emit_score
                if total > best_score:
                    best_score = total
                    best_prev = prev_midi
            if best_score > NEG_INF:
                dp[i][midi] = (best_score, best_prev)

    # Backtrace
    if not dp[-1]:
        raise ValueError("support voice DP found no valid path")
    chosen: list[int] = [0] * len(all_spans)
    chosen[-1] = max(dp[-1], key=lambda m: dp[-1][m][0])
    for i in range(len(all_spans) - 1, 0, -1):
        chosen[i - 1] = dp[i][chosen[i]][1]  # type: ignore[assignment]

    # Build output events
    result: list[GeneratedEvent] = []
    provenance: list[dict] = []
    for i, (span, midi) in enumerate(zip(all_spans, chosen)):
        emit_score, emit_reasons = _emit(midi, span)
        prev_midi = chosen[i - 1] if i > 0 else None
        prev_melody = all_spans[i - 1]["melody_midi"] if i > 0 else None
        prev_ic = (
            (prev_melody - prev_midi) % 12
            if prev_midi is not None and prev_melody is not None
            else None
        )
        _, trans_reasons, _ = _transit(
            midi, prev_midi,
            span["melody_midi"], prev_melody,
            prev_ic, span["is_sub"],
        )
        event_start = span["start"]
        event_duration = span["duration"]
        event_end = event_start + event_duration
        source_events = [
            event.id for event in song.melody
            if event.start < event_end and event.end > event_start
        ]
        language_reason = (
            f"rhythm {common_duration} borrowed from source vocabulary"
            if span["borrowed_rhythm"]
            else "sustained against active source rhythm"
        )
        all_reasons = list(dict.fromkeys([*emit_reasons, *trans_reasons]))
        ev = GeneratedEvent(
            id=f"support-{span['harmonic_index']}-{span['sub_index']}",
            start=event_start,
            duration=event_duration,
            pitch=Pitch.from_midi(midi, prefer_flats=prefer_flats),
            role="bass" if midi < 60 else "inner_harmony",
            source_ids=source_events,
            rationale=[
                f"{span['harmonic_slice'].id}: degree {span['harmonic_slice'].selected_degree}",
                language_reason,
                *all_reasons,
            ],
        )
        result.append(ev)
        provenance.append({
            "event_id": ev.id,
            "kind": "source-language-conditioned-generation",
            "harmony_id": span["harmonic_slice"].id,
            "source_event_ids": source_events,
            "rationale": ev.rationale,
        })
    return result, provenance


def validate_arrangement(arrangement: ArrangementIR) -> list[str]:
    errors: list[str] = []
    for voice in arrangement.voices:
        previous: GeneratedEvent | None = None
        for event in sorted(voice.events, key=lambda item: item.start):
            if event.duration <= 0:
                errors.append(f"{event.id}: duration must be positive")
            if not voice.profile.min_midi <= event.pitch.midi <= voice.profile.max_midi:
                errors.append(
                    f"{event.id}: MIDI {event.pitch.midi} outside {voice.profile.id} range"
                )
            if previous is not None and event.start < previous.end:
                errors.append(f"{event.id}: overlaps {previous.id} in monophonic voice")
            previous = event
    return errors


def arrange_support(
    source: SourceScore,
    song: SongIR,
    *,
    voice1_profile: str = "violin",
    voice2_profile: str = "cello",
    seed: int = 1,
) -> ArrangementIR:
    profile1 = get_profile(voice1_profile)
    profile2 = get_profile(voice2_profile)
    prefer_flats = source.fifths < 0
    melody_events, melody_diagnostics = generated_from_source(
        song.melody,
        profile_min=profile1.min_midi,
        profile_max=profile1.max_midi,
        prefer_flats=prefer_flats,
        phrase_boundaries={
            boundary for phrase in song.phrases
            for boundary in (phrase.start, phrase.end)
        },
    )
    support_events, provenance = generate_support_voice(
        song, profile2, seed=seed, prefer_flats=prefer_flats
    )
    arrangement = ArrangementIR(
        voices=[
            OutputVoice(id="voice1", profile=profile1, events=melody_events),
            OutputVoice(id="voice2", profile=profile2, events=support_events),
        ],
        relationship="support",
        diagnostics=melody_diagnostics,
        provenance=[
            {
                "event_id": event.id,
                "kind": "source-melody",
                "source_event_ids": event.source_ids,
                "rationale": event.rationale,
            }
            for event in melody_events
        ] + provenance,
    )
    hard_errors = validate_arrangement(arrangement)
    if hard_errors:
        raise ValueError("arrangement violates hard constraints: " + "; ".join(hard_errors))
    return arrangement
