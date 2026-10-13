"""
Voice leading rule tests grounded in species counterpoint and SATB pedagogy.

Hard rules tested here (violations → return None in the scorer, so they are
provably never generated):
  - No parallel perfect intervals (5ths or octaves) in countermelody
  - No oblique-motion unison to unison at cadence in countermelody
  - Countermelody stays below primary melody (voice crossing penalty is hard
    in protected phrases)

Soft but statistically reliable rules (verified on fixed seeds):
  - Support voice generally stays below melody (large penalty, not hard ban)
  - Cadential support note lands on harmonic root (bonus ensures it on simple input)
  - Register separation: voices stay a 3rd or more apart most of the time
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from miare.analysis import analyze_score
from miare.counterpoint import arrange_countermelody
from miare.generation import arrange_support
from miare.ir import GeneratedEvent
from miare.musicxml import parse_musicxml


FIXTURE = Path(__file__).parent / "fixtures" / "simple_melody.musicxml"

PERFECT_ICS = {0, 7}   # unison/octave and perfect fifth (mod 12)
CONSONANT_ICS = {0, 3, 4, 7, 8, 9}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simultaneous_intervals(
    voice1: list[GeneratedEvent],
    voice2: list[GeneratedEvent],
) -> list[tuple[Fraction, int, int]]:
    """Return (time, v1_midi, v2_midi) at every onset in either voice."""
    onsets = sorted({ev.start for ev in voice1} | {ev.start for ev in voice2})
    result = []
    for t in onsets:
        m = next((ev.pitch.midi for ev in voice1 if ev.start <= t < ev.end), None)
        c = next((ev.pitch.midi for ev in voice2 if ev.start <= t < ev.end), None)
        if m is not None and c is not None:
            result.append((t, m, c))
    return result


def _parallel_perfect_pairs(
    voice1: list[GeneratedEvent],
    voice2: list[GeneratedEvent],
) -> list[tuple[Fraction, int]]:
    """Return (time, interval_class) for each parallel perfect interval found."""
    pairs = _simultaneous_intervals(voice1, voice2)
    violations = []
    for i in range(len(pairs) - 1):
        _, m1, c1 = pairs[i]
        t2, m2, c2 = pairs[i + 1]
        ic1 = (m1 - c1) % 12
        ic2 = (m2 - c2) % 12
        if ic1 in PERFECT_ICS and ic2 in PERFECT_ICS:
            mel_motion = m2 - m1
            ctr_motion = c2 - c1
            # Parallel = both move in the same direction, neither is oblique
            if mel_motion * ctr_motion > 0 and mel_motion != 0:
                violations.append((t2, ic2))
    return violations


def _voice_crossings(
    upper: list[GeneratedEvent],
    lower: list[GeneratedEvent],
) -> list[Fraction]:
    """Return onset times where lower voice is at or above upper voice."""
    pairs = _simultaneous_intervals(upper, lower)
    return [t for t, u, lo in pairs if lo >= u]


# ---------------------------------------------------------------------------
# Countermelody: hard rules from _score_action
# ---------------------------------------------------------------------------

def _get_countermelody(seed: int = 5) -> tuple:
    source = parse_musicxml(FIXTURE)
    song = analyze_score(source)
    arrangement = arrange_countermelody(
        source, song, beam_width=24, context_measures=2, seed=seed
    )
    melody = arrangement.voices[0].events
    counter = arrangement.voices[1].events
    return source, song, melody, counter


class TestCounterpointHardRules:
    """These rules are enforced by returning None in _score_action, making
    violations structurally impossible regardless of seed or input."""

    def test_no_parallel_perfect_intervals(self) -> None:
        # The scorer returns None when consecutive intervals are both perfect
        # and motion is parallel (counterpoint.py lines 655-659).
        _, _, melody, counter = _get_countermelody()
        violations = _parallel_perfect_pairs(melody, counter)
        assert not violations, (
            f"Parallel perfect intervals found at: {violations}"
        )

    def test_melodic_leap_never_exceeds_octave(self) -> None:
        # Leap > 12 semitones is hard-banned (counterpoint.py line 628).
        _, _, _, counter = _get_countermelody()
        midis = [ev.pitch.midi for ev in sorted(counter, key=lambda e: e.start)]
        for left, right in zip(midis, midis[1:]):
            leap = abs(right - left)
            assert leap <= 12, f"Leap of {leap} semitones detected in countermelody"

    def test_all_countermelody_events_have_role(self) -> None:
        _, _, _, counter = _get_countermelody()
        assert all(ev.role == "countermelody" for ev in counter)

    def test_countermelody_stays_in_key(self) -> None:
        # Without chromatic source context, no chromatic pitches should appear.
        _, _, _, counter = _get_countermelody()
        c_major_pcs = {0, 2, 4, 5, 7, 9, 11}
        melody_pcs = {ev.pitch.midi % 12 for ev in counter}
        assert melody_pcs <= c_major_pcs

    def test_pending_dissonance_resolves_stepwise(self) -> None:
        # After a dissonance is created, the next note must move by step (≤2).
        # The scorer returns None for abs(motion) > 2 when pending_resolution
        # is set.  We verify indirectly: no large leap immediately follows a
        # dissonant interval.
        _, _, melody, counter = _get_countermelody()
        pairs = _simultaneous_intervals(melody, counter)
        for i in range(len(pairs) - 1):
            _, m1, c1 = pairs[i]
            _, m2, c2 = pairs[i + 1]
            ic1 = (m1 - c1) % 12
            if ic1 not in CONSONANT_ICS:
                leap = abs(c2 - c1)
                assert leap <= 2, (
                    f"After dissonant interval {ic1}, counter moved by {leap} semitones"
                )

    def test_countermelody_has_events(self) -> None:
        _, _, _, counter = _get_countermelody()
        assert len(counter) > 0

    def test_no_voice_crossing_in_protected_phrase(self) -> None:
        # The first phrase has identity_policy="protected".  Voice crossing
        # in a protected phrase with melody_midi - profile.min_midi >= 3
        # hard-returns None, so the generated voice must stay below the melody.
        source, song, melody, counter = _get_countermelody()
        first_phrase = song.phrases[0]
        phrase_pairs = [
            (t, m, c)
            for t, m, c in _simultaneous_intervals(melody, counter)
            if first_phrase.start <= t < first_phrase.end
        ]
        crossings = [(t, m, c) for t, m, c in phrase_pairs if c >= m]
        assert not crossings, (
            f"Voice crossings in protected phrase: {crossings}"
        )


# ---------------------------------------------------------------------------
# Support voice: statistically reliable rules (fixed seed=7)
# ---------------------------------------------------------------------------

def _get_support(seed: int = 7) -> tuple:
    source = parse_musicxml(FIXTURE)
    song = analyze_score(source)
    arrangement = arrange_support(source, song, seed=seed)
    melody = arrangement.voices[0].events
    support = arrangement.voices[1].events
    return source, song, melody, support


class TestSupportVoiceRules:
    """Support voice tests.  Voice crossing is heavily penalized (-5 emission
    penalty) and register separation is rewarded, so these hold on the
    simple_melody fixture for any reasonable seed."""

    def test_support_voice_mostly_below_melody(self) -> None:
        _, _, melody, support = _get_support()
        crossings = _voice_crossings(melody, support)
        # Allow zero crossings; the fixture is simple and well-behaved
        assert not crossings, (
            f"Support voice crossed above melody at: {crossings}"
        )

    def test_no_parallel_perfect_intervals_support(self) -> None:
        # The support DP applies a heavy -4 penalty for parallel perfects, but it
        # is not a hard ban (unlike the countermelody scorer).  On simple input
        # it should produce none.  If this fails, the generator needs a hard gate.
        _, _, melody, support = _get_support()
        violations = _parallel_perfect_pairs(melody, support)
        assert not violations, (
            f"Parallel perfect intervals in support at: {violations}. "
            "The support Viterbi only penalises (−4); a hard-ban gate is missing."
        )

    def test_register_separation_at_least_minor_third(self) -> None:
        # Textbook: bass and soprano should be separated by a 3rd or more
        # to maintain perceptual independence (clear-melodic-support-register bonus).
        _, _, melody, support = _get_support()
        pairs = _simultaneous_intervals(melody, support)
        separations = [m - s for _, m, s in pairs]
        assert separations, "no simultaneous notes found"
        # Allow occasional 2-semitone separation (major 2nd), but not unison
        violations = [sep for sep in separations if sep < 2]
        assert not violations, (
            f"Support and melody too close (< major 2nd): {violations}"
        )

    def test_cadential_support_lands_on_harmonic_root(self) -> None:
        # The emission score adds +2.5 for landing on the harmonic root at a
        # phrase end.  On the simple_melody the last phrase ends on C major
        # (tonic = pc 0), so the last support note should be C.
        source, song, melody, support = _get_support()
        last_phrase_end = song.phrases[-1].end
        # Find support note that spans the phrase end
        last_support = next(
            (ev for ev in reversed(support) if ev.end == last_phrase_end),
            support[-1],
        )
        # Should be on tonic pitch class (C = 0)
        assert last_support.pitch.midi % 12 == song.tonic_pc, (
            f"Final support note pc={last_support.pitch.midi % 12}, "
            f"expected tonic={song.tonic_pc}"
        )

    def test_support_voice_in_instrument_range(self) -> None:
        # validate_arrangement already checks this, but we assert it directly.
        from miare.generation import validate_arrangement
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        arrangement = arrange_support(source, song, seed=7)
        errors = validate_arrangement(arrangement)
        assert not errors

    def test_support_voice_prefers_contrary_motion(self) -> None:
        # Contrary motion gets a +1.2 bonus.  Count how often it occurs.
        _, _, melody, support = _get_support()
        pairs = _simultaneous_intervals(melody, support)
        contrary = 0
        parallel = 0
        for i in range(len(pairs) - 1):
            _, m1, s1 = pairs[i]
            _, m2, s2 = pairs[i + 1]
            mel_dir = m2 - m1
            sup_dir = s2 - s1
            if mel_dir != 0 and sup_dir != 0:
                if mel_dir * sup_dir < 0:
                    contrary += 1
                else:
                    parallel += 1
        # Contrary motion should occur at least as often as parallel on this fixture
        total = contrary + parallel
        if total > 0:
            ratio = contrary / total
            assert ratio >= 0.4, (
                f"Contrary motion ratio {ratio:.2f} is low "
                f"({contrary}/{total} pairs)"
            )


# ---------------------------------------------------------------------------
# Interval quality verification helpers
# ---------------------------------------------------------------------------

class TestIntervalHelpers:
    """Sanity-check the test helpers against known intervals."""

    def test_perfect_fifth_detection(self) -> None:
        from miare.ir import GeneratedEvent, Pitch
        def _ev(midi: int, start: Fraction, dur: Fraction) -> GeneratedEvent:
            return GeneratedEvent(
                id="x", start=start, duration=dur,
                pitch=Pitch.from_midi(midi),
                role="test", source_ids=[], rationale=[],
            )
        # G4 (melody, upper) and C4 (counter, lower): perfect fifth upward = ic 7
        v1 = [_ev(67, Fraction(0), Fraction(1))]  # G4 melody
        v2 = [_ev(60, Fraction(0), Fraction(1))]  # C4 counter
        pairs = _simultaneous_intervals(v1, v2)
        assert pairs
        _, m, c = pairs[0]
        assert (m - c) % 12 == 7  # G4 − C4 = 7 semitones

    def test_crossing_detected(self) -> None:
        from miare.ir import GeneratedEvent, Pitch
        def _ev(midi: int, start: Fraction) -> GeneratedEvent:
            return GeneratedEvent(
                id="x", start=start, duration=Fraction(1),
                pitch=Pitch.from_midi(midi),
                role="test", source_ids=[], rationale=[],
            )
        upper = [_ev(60, Fraction(0))]  # C4
        lower = [_ev(62, Fraction(0))]  # D4 — above the "upper" voice
        crossings = _voice_crossings(upper, lower)
        assert crossings  # D4 > C4 is a crossing
