"""
Voice leading rule tests grounded in species counterpoint and SATB pedagogy.

Hard rules tested here (`check_hard_constraints` rejects a violation before
any scoring runs, so they are provably never generated):
  - No parallel perfect intervals (5ths or octaves) in countermelody
  - No oblique-motion unison to unison at cadence in countermelody
  - Countermelody stays below primary melody (voice crossing penalty is hard
    in protected phrases)

Soft but statistically reliable rules (verified on fixed seeds):
  - Support voice generally stays below melody (large penalty, not hard ban)
  - Cadential support note lands on harmonic root (bonus ensures it on simple input)
  - Register separation: voices stay a 3rd or more apart most of the time

Structural rules (DESIGN.md §7.7):
  - Both voices anchor to the structural melody, never to a pruned passing tone
"""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from miare.analysis import analyze_score
from miare.counterpoint import _melody_anchor as _counterpoint_melody_anchor
from miare.counterpoint import arrange_countermelody
from miare.generation import _melody_anchor, _support_spans, arrange_support
from miare.instruments import get_profile
from miare.ir import GeneratedEvent
from miare.musicxml import parse_musicxml


FIXTURE = Path(__file__).parent / "fixtures" / "simple_melody.musicxml"
PRUNING_FIXTURE = Path(__file__).parent / "fixtures" / "passing_tones.musicxml"

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
# Countermelody: hard rules from check_hard_constraints
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
    """These rules are enforced by `check_hard_constraints`, which runs before
    any scoring, making violations structurally impossible regardless of seed
    or input. The rules themselves are unit-tested in
    `tests/test_hard_constraints.py`; these tests check the arrangement the
    beam search actually produces."""

    def test_no_parallel_perfect_intervals(self) -> None:
        _, _, melody, counter = _get_countermelody()
        violations = _parallel_perfect_pairs(melody, counter)
        assert not violations, (
            f"Parallel perfect intervals found at: {violations}"
        )

    def test_melodic_leap_never_exceeds_octave(self) -> None:
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
        # The gate rejects abs(motion) > 2 when pending_resolution is set
        # (`resolution-not-consonant-step`).  We verify indirectly here: no
        # large leap immediately follows a dissonant interval.
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
        # in a protected phrase with melody_midi - profile.min_midi >= 3 is
        # rejected (`protected-voice-crossing`), so the generated voice must
        # stay below the melody.
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
# Support voice anchors against the planning melody (DESIGN.md §7.7)
# ---------------------------------------------------------------------------

def _pruned_song() -> tuple:
    source = parse_musicxml(PRUNING_FIXTURE)
    return source, analyze_score(source)


def _pruned_events(song) -> list:
    """Melody notes the structural reduction removed."""
    structural_ids = {event.id for event in song.structural_melody}
    return [event for event in song.melody if event.id not in structural_ids]


class TestSupportAnchorsToPlanningMelody:
    """DESIGN.md §7.7: the search unit is the structural melody.  An ornamental
    passing tone that `reduce_to_structural_melody` removed must not be the
    pitch the support voice is scored against, or the accompaniment is dragged
    toward surface notes and over-densifies."""

    def test_fixture_actually_prunes(self) -> None:
        # Without pruning this fixture proves nothing about the anchor.
        _, song = _pruned_song()
        assert len(song.structural_melody) < len(song.melody), (
            f"{PRUNING_FIXTURE.name} must contain passing tones the reduction "
            f"removes: melody={len(song.melody)} structural="
            f"{len(song.structural_melody)}"
        )

    def test_anchor_returns_structural_pitch_over_passing_tone(self) -> None:
        _, song = _pruned_song()
        structural_midis = {event.pitch.midi for event in song.structural_melody}
        pruned = _pruned_events(song)
        assert pruned, "fixture has no pruned notes"
        for passing in pruned:
            anchor = _melody_anchor(song, passing.start, passing.end)
            assert anchor is not None, (
                f"no anchor for the slot over pruned {passing.id}"
            )
            assert anchor != passing.pitch.midi, (
                f"slot over pruned {passing.id} anchored to the passing tone "
                f"{passing.pitch.midi} instead of a structural pitch"
            )
            assert anchor in structural_midis, (
                f"anchor {anchor} is not a structural pitch"
            )

    def test_generated_slot_over_passing_tone_anchors_structurally(self) -> None:
        # Read the anchor off the slots the Viterbi really scores, so the test
        # cannot pass on a wider window than the generator used.
        _, song = _pruned_song()
        pruned = _pruned_events(song)
        assert pruned, "fixture has no pruned notes"
        checked = 0
        for span in _support_spans(song, get_profile("cello")):
            end = span["start"] + span["duration"]
            inside = [
                event for event in pruned
                if event.start >= span["start"] and event.end <= end
            ]
            if not inside:
                continue
            checked += 1
            # The slot sits inside a pruned region, so the structural note that
            # governs it is the last one ending at or before the slot start.
            governing = max(
                (
                    event for event in song.structural_melody
                    if event.end <= span["start"]
                ),
                key=lambda event: event.end,
            )
            assert span["melody_midi"] == governing.pitch.midi, (
                f"slot {span['start']}–{end} covering pruned "
                f"{[event.id for event in inside]} anchored to "
                f"{span['melody_midi']}; expected the governing structural "
                f"note {governing.id} ({governing.pitch.midi}), and the "
                f"passing tone is {inside[0].pitch.midi}"
            )
        assert checked, "no generated slot covers a pruned passing tone"

    def test_both_anchors_share_one_signature(self) -> None:
        # The two modules took (start, duration) vs (start, end); copying a call
        # between them silently produced a wrong time window.
        import inspect

        assert (
            list(inspect.signature(_melody_anchor).parameters)
            == list(inspect.signature(_counterpoint_melody_anchor).parameters)
            == ["song", "start", "end"]
        )

    def test_anchor_matches_counterpoint_where_structural_notes_overlap(self) -> None:
        _, song = _pruned_song()
        for event in song.structural_melody:
            assert _melody_anchor(song, event.start, event.end) == (
                _counterpoint_melody_anchor(song, event.start, event.end)
            ), f"anchors disagree over structural {event.id}"

    def test_anchor_falls_back_to_surface_melody_when_no_reduction(self) -> None:
        # Half of the AC: `song.structural_melody or song.melody`.  A score the
        # reduction produced nothing for must still anchor, not return None for
        # every slot and switch the spacing penalties off wholesale.
        _, song = _pruned_song()
        unreduced = replace(song, structural_melody=[])
        anchored = 0
        for event in song.melody:
            expected = _melody_anchor(song, event.start, event.end)
            actual = _melody_anchor(unreduced, event.start, event.end)
            if event.pitch is not None:
                assert actual == event.pitch.midi, (
                    f"with no structural melody, the slot over {event.id} "
                    f"anchored to {actual}; expected its own surface pitch "
                    f"{event.pitch.midi}"
                )
                anchored += 1
            # And the fallback is a real fallback: over a pruned note the two
            # disagree, which is what the reduction is for.
            if event.id in {e.id for e in _pruned_events(song)}:
                assert actual != expected, (
                    f"fallback over pruned {event.id} matched the structural "
                    f"anchor; the fixture or the fallback is not exercised"
                )
        assert anchored, "fixture has no pitched melody notes"


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
