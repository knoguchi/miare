from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
from math import sqrt
from statistics import mean

from .ir import (
    Event,
    HarmonicSlice,
    HarmonyCandidate,
    MusicalLanguage,
    Phrase,
    SongIR,
    SourceScore,
    fraction_text,
)
from .theory import measure_for_time, scale_intervals


MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def tonic_from_signature(fifths: int, mode: str) -> int:
    major = (0, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10, 5)
    minor = (9, 4, 11, 6, 1, 8, 3, 10, 5, 0, 7, 2)
    index = fifths % 12
    return (minor if mode.lower().startswith("min") else major)[index]


def _correlation(histogram: list[float], profile: tuple[float, ...], shift: int) -> float:
    rotated = [profile[(pitch_class - shift) % 12] for pitch_class in range(12)]
    hist_mean = mean(histogram)
    profile_mean = mean(rotated)
    numerator = sum(
        (value - hist_mean) * (weight - profile_mean)
        for value, weight in zip(histogram, rotated)
    )
    denominator = sqrt(
        sum((value - hist_mean) ** 2 for value in histogram)
        * sum((weight - profile_mean) ** 2 for weight in rotated)
    )
    return numerator / denominator if denominator else 0.0


def infer_key(events: list[Event]) -> tuple[int, str, float]:
    histogram = [0.0] * 12
    for event in events:
        if event.pitch is not None:
            histogram[event.pitch.midi % 12] += float(event.duration)
    choices: list[tuple[float, int, str]] = []
    for tonic in range(12):
        choices.append((_correlation(histogram, MAJOR_PROFILE, tonic), tonic, "major"))
        choices.append((_correlation(histogram, MINOR_PROFILE, tonic), tonic, "minor"))
    score, tonic, mode = max(choices)
    return tonic, mode, score


def select_melody(score: SourceScore) -> tuple[str, str, list[Event], list[str]]:
    candidates: list[tuple[float, str, str, list[Event], list[str]]] = []
    for part in score.parts:
        voices: dict[str, list[Event]] = defaultdict(list)
        for event in part.events:
            if event.pitch is not None:
                voices[event.voice].append(event)
        for voice, events in voices.items():
            if not events:
                continue
            # Simultaneous chord tones are reduced only for melodic-line analysis.
            by_start: dict[Fraction, Event] = {}
            for event in events:
                previous = by_start.get(event.start)
                if previous is None or event.pitch.midi > previous.pitch.midi:
                    by_start[event.start] = event
            line = sorted(by_start.values(), key=lambda event: (event.start, event.pitch.midi))
            total_duration = sum((event.duration for event in line), Fraction(0))
            pitch_mean = mean(event.pitch.midi for event in line)
            name = part.name.lower()
            name_bonus = 12.0 if any(
                word in name for word in ("melody", "vocal", "voice", "soprano", "lead")
            ) else 0.0
            monophony_ratio = len(line) / len(events)
            continuity = 0.0
            if len(line) > 1:
                intervals = [
                    abs(right.pitch.midi - left.pitch.midi)
                    for left, right in zip(line, line[1:])
                ]
                continuity = sum(1 for interval in intervals if interval <= 7) / len(intervals)
            candidate_score = (
                float(total_duration)
                + name_bonus
                + monophony_ratio * 5.0
                + continuity * 3.0
                + pitch_mean / 100.0
            )
            reasons = [
                f"part={part.name}",
                f"voice={voice}",
                f"monophony={monophony_ratio:.2f}",
                f"stepwise-continuity={continuity:.2f}",
            ]
            candidates.append((candidate_score, part.id, voice, line, reasons))
    if not candidates:
        raise ValueError("no melodic line candidates")
    _, part_id, voice, line, reasons = max(candidates, key=lambda candidate: candidate[0])
    diagnostics = [
        f"primary melody selected from {part_id} voice {voice}: " + ", ".join(reasons)
    ]
    return part_id, voice, line, diagnostics


def detect_phrases(score: SourceScore, melody: list[Event], tonic_pc: int) -> list[Phrase]:
    if not melody:
        return []
    boundary_events: list[tuple[Fraction, list[str]]] = []
    for index, event in enumerate(melody[:-1]):
        following = melody[index + 1]
        measure = measure_for_time(score, event.start)
        beat = Fraction(4, measure.beat_type)
        gap = following.start - event.end
        evidence: list[str] = []
        # Require at least a full beat of silence.  Brief grace-note gaps
        # or sub-beat rests inside a phrase should not split the phrase.
        if gap >= beat * 2:
            evidence.append(f"rest/gap={fraction_text(gap)}")
        # A "long note" boundary only fires when the note fills ≥70% of the
        # measure.  Using a fixed 2-beat threshold over-splits slow pieces
        # (e.g. 6/4 time) where half notes are the norm, not the exception.
        if event.duration >= measure.duration * Fraction(7, 10):
            evidence.append(f"long-note={fraction_text(event.duration)}")
        if evidence:
            boundary_events.append((event.end, evidence))

    # Four-measure groups are weak candidates when notation has no explicit breathing gap.
    for index, measure in enumerate(score.measures, 1):
        if index % 4 == 0 and measure.end < score.measures[-1].end:
            nearby = [event for event in melody if abs(float(event.end - measure.end)) < 1e-8]
            if nearby:
                boundary_events.append((measure.end, ["four-measure grouping"]))

    boundary_events.append((melody[-1].end, ["end of melody"]))
    merged: dict[Fraction, list[str]] = defaultdict(list)
    for time, evidence in boundary_events:
        merged[time].extend(evidence)

    # Minimum phrase length = 2 full measures.  Short segments created by
    # 4-bar groupings landing mid-phrase, or brief rests that slip through,
    # get merged forward into the next segment (or backward into the previous).
    avg_measure_dur = (
        sum(m.duration for m in score.measures) / len(score.measures)
        if score.measures else Fraction(4)
    )
    min_phrase_duration = avg_measure_dur * 2

    raw_phrases: list[tuple[Fraction, Fraction, list[Event], list[str]]] = []
    start = melody[0].start
    for end in sorted(merged):
        if end <= start:
            continue
        notes = [event for event in melody if start <= event.start < end]
        if not notes:
            continue
        raw_phrases.append((start, end, notes, list(dict.fromkeys(merged[end]))))
        later_notes = [event for event in melody if event.start >= end]
        start = later_notes[0].start if later_notes else end

    # Merge any phrase that is shorter than or equal to min_phrase_duration into
    # the next (unless it is the only phrase, or the last phrase).
    # Using <= so that exactly-min-sized segments (e.g. exactly 2 measures) are
    # also pulled into the next, since they are too short for a good arrangement.
    merged_phrases: list[tuple[Fraction, Fraction, list[Event], list[str]]] = []
    pending: tuple[Fraction, Fraction, list[Event], list[str]] | None = None
    for seg_start, seg_end, notes, evidence in raw_phrases:
        if pending is not None:
            ps, pe, pn, pev = pending
            if pe - ps <= min_phrase_duration:
                # Absorb into next segment
                pending = (ps, seg_end, pn + notes, pev + evidence)
                continue
            merged_phrases.append(pending)
        pending = (seg_start, seg_end, notes, evidence)
    if pending is not None:
        ps, pe, pn, pev = pending
        if merged_phrases and pe - ps <= min_phrase_duration:
            # Last segment is too small — merge back into the previous
            prev_s, prev_e, prev_n, prev_ev = merged_phrases[-1]
            merged_phrases[-1] = (prev_s, pe, prev_n + pn, prev_ev + pev)
        else:
            merged_phrases.append(pending)

    phrases: list[Phrase] = []
    for seg_start, seg_end, notes, evidence in merged_phrases:
        last_pc = notes[-1].pitch.midi % 12
        if last_pc == tonic_pc:
            cadence = "tonic-arrival"
        elif last_pc == (tonic_pc + 7) % 12:
            cadence = "dominant-arrival"
        else:
            cadence = "open"
        phrases.append(
            Phrase(
                id=f"phrase-{len(phrases) + 1}",
                start=seg_start,
                end=seg_end,
                apex_midi=max(event.pitch.midi for event in notes),
                cadence=cadence,
                evidence=list(dict.fromkeys(evidence)),
            )
        )
    return phrases


def _scale(mode: str) -> tuple[int, ...]:
    return scale_intervals(mode)


def _chord_vocabulary(tonic_pc: int, mode: str) -> list[tuple[int, set[int], str]]:
    """Diatonic triads (degrees 1-7) plus chromatic extensions (degrees 8-9)."""
    scale = _scale(mode)
    chords: list[tuple[int, set[int], str]] = []
    for index in range(7):
        root_pc = (tonic_pc + scale[index]) % 12
        third_pc = (tonic_pc + scale[(index + 2) % 7]) % 12
        fifth_pc = (tonic_pc + scale[(index + 4) % 7]) % 12
        pcs = {root_pc, third_pc, fifth_pc}
        offsets = sorted((pc - root_pc) % 12 for pc in pcs)
        quality = (
            "major" if 4 in offsets and 7 in offsets
            else "minor" if 3 in offsets and 7 in offsets
            else "diminished"
        )
        chords.append((root_pc, pcs, quality))
    # Secondary dominant V/V: major triad on the supertonic
    vv_root = (tonic_pc + 2) % 12
    chords.append((vv_root, {vv_root, (vv_root + 4) % 12, (vv_root + 7) % 12}, "major"))
    if mode.startswith("min"):
        # Harmonic-minor dominant: major triad on scale degree 5 (raised leading tone)
        hv_root = (tonic_pc + 7) % 12
        chords.append((hv_root, {hv_root, (hv_root + 4) % 12, (hv_root + 7) % 12}, "major"))
    else:
        # Borrowed subtonic bVII from parallel minor
        bvii_root = (tonic_pc + 10) % 12
        chords.append((bvii_root, {bvii_root, (bvii_root + 4) % 12, (bvii_root + 7) % 12}, "major"))
    return chords


def _transition(left: int, right: int) -> float:
    tonic = {1, 3, 6}
    predominant = {2, 4}
    dominant = {5, 7}
    if left in dominant and right == 1:
        return 1.2
    if left in predominant and right in dominant:
        return 0.9
    if left in tonic and right in predominant:
        return 0.6
    if left == 8 and right in dominant:  # V/V → V: secondary dominant resolves
        return 1.0
    if left == 8 and right == 1:  # V/V → I: passing
        return 0.3
    if left == 9 and right == 1:  # harmonic-V or bVII → I
        return 0.8
    if left == 9 and right in dominant:
        return 0.4
    if left == 9 and right in predominant:
        return 0.3
    if left == right:
        return 0.15
    if right == 1:
        return 0.25
    return -0.1


def infer_harmony(
    score: SourceScore,
    melody: list[Event],
    phrases: list[Phrase],
    tonic_pc: int,
    mode: str,
) -> list[HarmonicSlice]:
    spans: list[tuple[Fraction, Fraction]] = []
    for measure in score.measures:
        if measure.beats >= 4 and measure.duration >= 4:
            half = measure.duration / 2
            spans.extend(((measure.start, half), (measure.start + half, half)))
        else:
            spans.append((measure.start, measure.duration))

    chords = _chord_vocabulary(tonic_pc, mode)
    n_chords = len(chords)
    melody_ids = {event.id for event in melody}
    inner_events = [
        event for part in score.parts
        for event in part.events
        if event.pitch is not None and event.id not in melody_ids
    ]
    local_scores: list[list[float]] = []
    candidate_rows: list[list[HarmonyCandidate]] = []
    phrase_ends = {phrase.end for phrase in phrases}

    for span_index, (start, duration) in enumerate(spans):
        end = start + duration
        notes = [event for event in melody if event.start < end and event.end > start]
        inner_notes = [event for event in inner_events if event.start < end and event.end > start]
        row: list[HarmonyCandidate] = []
        row_scores: list[float] = []
        scale_pcs = set((tonic_pc + step) % 12 for step in _scale(mode))
        for degree, (root_pc, chord_pcs, quality) in enumerate(chords, 1):
            value = 0.0
            reasons: list[str] = []
            # Chromatic chords (degrees 8-9) need explicit melodic support to be
            # selected; without a clear chromatic melody note they almost always
            # represent misidentified inner-voice passing tones.
            if degree > 7:
                chromatic_pcs = chord_pcs - scale_pcs
                if not any(event.pitch.midi % 12 in chromatic_pcs for event in notes):
                    value -= 2.0
            for event in notes:
                overlap = min(event.end, end) - max(event.start, start)
                measure = measure_for_time(score, event.start)
                local_onset = event.start - measure.start
                metric_weight = 1.8 if local_onset == 0 else 1.2
                if event.pitch.midi % 12 in chord_pcs:
                    value += float(overlap) * metric_weight
                else:
                    value -= float(overlap) * 0.35
            for event in inner_notes:
                overlap = float(min(event.end, end) - max(event.start, start))
                if event.pitch.midi % 12 in chord_pcs:
                    value += overlap * 1.6
                    if not reasons or "inner voice" not in reasons[-1]:
                        reasons.append("inner voice supports chord")
                else:
                    value -= overlap * 0.15
            if start == 0 and degree == 1:
                value += 0.8
                reasons.append("opening tonic")
            if end in phrase_ends:
                if degree == 1:
                    value += 1.2
                    reasons.append("phrase-end tonic arrival")
                elif degree == 5:
                    value += 0.5
                    reasons.append("phrase-end dominant arrival")
            if any(event.pitch.midi % 12 == root_pc for event in notes):
                reasons.append("root present in melody")
            row.append(
                HarmonyCandidate(
                    degree,
                    root_pc,
                    quality,
                    value,
                    pcs=frozenset(chord_pcs),
                    reasons=reasons,
                )
            )
            row_scores.append(value)
        candidate_rows.append(row)
        local_scores.append(row_scores)

    if not candidate_rows:
        return []
    paths: list[list[tuple[float, int | None]]] = []
    paths.append([
        (score_value + (0.4 if degree == 0 else 0.0), None)
        for degree, score_value in enumerate(local_scores[0])
    ])
    for index in range(1, len(local_scores)):
        row: list[tuple[float, int | None]] = []
        for right_index, local_score in enumerate(local_scores[index]):
            options = [
                (
                    paths[index - 1][left_index][0]
                    + _transition(left_index + 1, right_index + 1)
                    + local_score,
                    left_index,
                )
                for left_index in range(n_chords)
            ]
            row.append(max(options, key=lambda option: option[0]))
        paths.append(row)

    selected = [0] * len(spans)
    selected[-1] = max(range(n_chords), key=lambda i: paths[-1][i][0])
    for index in range(len(spans) - 1, 0, -1):
        previous = paths[index][selected[index]][1]
        selected[index - 1] = 0 if previous is None else previous

    result: list[HarmonicSlice] = []
    for index, ((start, duration), candidates, degree_index) in enumerate(
        zip(spans, candidate_rows, selected), 1
    ):
        result.append(
            HarmonicSlice(
                id=f"harmony-{index}",
                start=start,
                duration=duration,
                candidates=sorted(candidates, key=lambda candidate: candidate.score, reverse=True),
                selected_degree=degree_index + 1,
            )
        )
    return result


def refine_phrases_with_harmony(
    melody: list[Event],
    rough_phrases: list[Phrase],
    harmony: list[HarmonicSlice],
    tonic_pc: int,
    score: SourceScore,
) -> list[Phrase]:
    """Split phrases at authentic cadences (V→I) not detected by notation alone.

    Only splits when every resulting sub-phrase would be at least 4 measures long.
    In waltz and slow-triple styles V→I appears every 2-3 measures, so splitting
    without a minimum guard produces 2-measure fragments unsuitable for arrangement.
    """
    avg_measure_dur = (
        sum(m.duration for m in score.measures) / len(score.measures)
        if score.measures else Fraction(4)
    )
    min_sub_duration = avg_measure_dur * 4  # require 4-measure sub-phrases after split

    existing_ends = {phrase.end for phrase in rough_phrases}
    new_boundaries: set[Fraction] = set()
    for index in range(len(harmony) - 1):
        current = harmony[index]
        following = harmony[index + 1]
        if current.selected_degree == 5 and following.selected_degree == 1:
            boundary = current.start + current.duration
            if boundary not in existing_ends and any(
                abs(float(event.start - boundary)) < 1e-8 for event in melody
            ):
                new_boundaries.add(boundary)
    if not new_boundaries:
        return rough_phrases
    refined: list[Phrase] = []
    for phrase in rough_phrases:
        inner = sorted(t for t in new_boundaries if phrase.start < t < phrase.end)
        if not inner:
            refined.append(phrase)
            continue
        split_points = [phrase.start, *inner, phrase.end]
        sub_durations = [split_points[i + 1] - split_points[i] for i in range(len(split_points) - 1)]
        if any(d < min_sub_duration for d in sub_durations):
            # Splitting would create a sub-minimum phrase — keep as one
            refined.append(phrase)
            continue
        for seg_start, seg_end in zip(split_points, split_points[1:]):
            notes = [event for event in melody if seg_start <= event.start < seg_end]
            if not notes:
                continue
            last_pc = notes[-1].pitch.midi % 12
            cadence = (
                "tonic-arrival" if last_pc == tonic_pc
                else "dominant-arrival" if last_pc == (tonic_pc + 7) % 12
                else "open"
            )
            refined.append(Phrase(
                id=f"phrase-{len(refined) + 1}",
                start=seg_start,
                end=seg_end,
                apex_midi=max(event.pitch.midi for event in notes),
                cadence=cadence,
                evidence=["harmonic-authentic-cadence"],
            ))
    for index, phrase in enumerate(refined, 1):
        phrase.id = f"phrase-{index}"
    return refined


def extract_musical_language(
    score: SourceScore,
    melody: list[Event],
    phrases: list[Phrase],
    harmony: list[HarmonicSlice],
    tonic_pc: int,
    mode: str,
) -> MusicalLanguage:
    rhythms = Counter(fraction_text(event.duration) for event in melody)
    accents: Counter[str] = Counter()
    for event in melody:
        measure = measure_for_time(score, event.start)
        beat = Fraction(4, measure.beat_type)
        position = (event.start - measure.start) / beat
        accents[f"beat-{fraction_text(position + 1)}"] += 1

    intervals = [
        right.pitch.midi - left.pitch.midi
        for left, right in zip(melody, melody[1:])
    ]
    motif_counts = Counter(tuple(intervals[index:index + 3]) for index in range(max(0, len(intervals) - 2)))
    motif_vocabulary = [
        {"intervals": list(intervals_key), "occurrences": count}
        for intervals_key, count in motif_counts.most_common(8)
        if count >= 2
    ]

    chord_tones: dict[str, frozenset[int]] = {}
    for harmonic_slice in harmony:
        chord_tones[harmonic_slice.id] = harmonic_slice.selected.pcs
    non_chord = 0
    considered = 0
    for event in melody:
        harmonic_slice = next(
            (
                item for item in harmony
                if item.start <= event.start < item.start + item.duration
            ),
            None,
        )
        if harmonic_slice is not None:
            considered += 1
            if event.pitch.midi % 12 not in chord_tones[harmonic_slice.id]:
                non_chord += 1

    density_curve = []
    for measure in score.measures:
        count = sum(1 for event in melody if measure.start <= event.start < measure.end)
        density_curve.append(round(count / float(measure.duration), 3))

    distinct_lines = {
        (part.id, event.voice)
        for part in score.parts
        for event in part.events
        if event.pitch is not None
    }
    unknown = []
    if len(distinct_lines) <= 1:
        unknown.extend(("bassIdioms", "texturePatterns", "interVoiceResponse"))

    evidence = [
        {
            "kind": "phrase",
            "id": phrase.id,
            "span": [fraction_text(phrase.start), fraction_text(phrase.end)],
            "cadence": phrase.cadence,
        }
        for phrase in phrases
    ]
    evidence.extend(
        {
            "kind": "motif",
            "intervals": motif["intervals"],
            "occurrences": motif["occurrences"],
        }
        for motif in motif_vocabulary
    )

    pitches = [event.pitch.midi for event in melody]
    return MusicalLanguage(
        phrase_lengths=[fraction_text(phrase.end - phrase.start) for phrase in phrases],
        rhythmic_vocabulary=dict(rhythms.most_common()),
        accent_patterns=dict(accents.most_common()),
        motif_vocabulary=motif_vocabulary,
        non_chord_tone_ratio=round(non_chord / considered, 3) if considered else 0.0,
        cadence_patterns=[phrase.cadence for phrase in phrases],
        register_usage={
            "minimum": min(pitches) if pitches else None,
            "maximum": max(pitches) if pitches else None,
            "apex": max(pitches) if pitches else None,
        },
        density_curve=density_curve,
        unknown_fields=unknown,
        evidence=evidence,
    )


def reduce_to_structural_melody(
    score: SourceScore,
    melody: list[Event],
    phrases: list[Phrase],
    harmony: list[HarmonicSlice],
) -> list[Event]:
    """Remove likely passing/neighbor tones for planning, not for output.

    The full melody remains available for notation. This reduction is the
    contrapuntal cantus-like skeleton used for anchors, phrase identity, and
    search density.
    """
    if len(melody) <= 2:
        return list(melody)
    phrase_boundaries = {
        boundary for phrase in phrases
        for boundary in (phrase.start, phrase.end)
    }
    result: list[Event] = []
    for index, event in enumerate(melody):
        if index == 0 or index == len(melody) - 1:
            result.append(event)
            continue
        previous = melody[index - 1]
        following = melody[index + 1]
        measure = measure_for_time(score, event.start)
        beat = Fraction(4, measure.beat_type)
        local_position = event.start - measure.start
        strong = local_position % beat == 0
        harmonic = next(
            (
                item for item in harmony
                if item.start <= event.start < item.start + item.duration
            ),
            None,
        )
        chord_pcs: frozenset[int] = frozenset()
        if harmonic is not None:
            chord_pcs = harmonic.selected.pcs
        is_neighbor = (
            previous.pitch.midi == following.pitch.midi
            and event.pitch.midi != previous.pitch.midi
        )
        incoming = event.pitch.midi - previous.pitch.midi
        outgoing = following.pitch.midi - event.pitch.midi
        is_stepwise_passing = (
            incoming * outgoing > 0
            and abs(incoming) <= 2
            and abs(outgoing) <= 2
        )
        if event.start in phrase_boundaries or event.end in phrase_boundaries:
            result.append(event)
        elif strong or event.duration >= beat or event.pitch.midi % 12 in chord_pcs:
            result.append(event)
        elif is_neighbor or is_stepwise_passing:
            continue
        elif event.duration <= beat / 2 and not chord_pcs:
            continue
        else:
            result.append(event)
    return result


def analyze_score(score: SourceScore) -> SongIR:
    part_id, voice, melody, diagnostics = select_melody(score)
    signature_mode = score.mode.lower()
    if signature_mode not in {"major", "minor"}:
        tonic_pc, mode, confidence = infer_key(melody)
        diagnostics.append(
            f"key inferred from pitch distribution: tonic_pc={tonic_pc}, mode={mode}, "
            f"correlation={confidence:.3f}"
        )
    else:
        tonic_pc = tonic_from_signature(score.fifths, signature_mode)
        mode = signature_mode
        diagnostics.append(
            f"key taken from MusicXML signature: tonic_pc={tonic_pc}, mode={mode}"
        )
    phrases_rough = detect_phrases(score, melody, tonic_pc)
    harmony_rough = infer_harmony(score, melody, phrases_rough, tonic_pc, mode)
    phrases = refine_phrases_with_harmony(melody, phrases_rough, harmony_rough, tonic_pc, score)
    harmony = infer_harmony(score, melody, phrases, tonic_pc, mode)
    structural_melody = reduce_to_structural_melody(
        score, melody, phrases, harmony
    )
    language = extract_musical_language(
        score, melody, phrases, harmony, tonic_pc, mode
    )
    return SongIR(
        tonic_pc=tonic_pc,
        mode=mode,
        melody_part_id=part_id,
        melody_voice=voice,
        melody=melody,
        structural_melody=structural_melody,
        phrases=phrases,
        harmony=harmony,
        musical_language=language,
        diagnostics=score.diagnostics + diagnostics,
    )
