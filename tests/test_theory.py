"""
Music theory unit tests grounded in common-practice harmony.

These tests verify the analysis layer against textbook rules:
  - Key-signature tonic mapping (circle of fifths)
  - Diatonic triad pitch-class content and quality
  - Functional harmonic transition weights
  - Structural melody reduction (passing and neighbor tones)
  - Phrase boundary detection from notation cues
"""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from miare.analysis import (
    _chord_vocabulary,
    _transition,
    analyze_score,
    reduce_to_structural_melody,
    tonic_from_signature,
)
from miare.counterpoint import _harmony_context
from miare.generation import _candidate_midis
from miare.instruments import get_profile
from miare.ir import (
    Event,
    HarmonyCandidate,
    Measure,
    Phrase,
    Pitch,
    SourceScore,
)
from miare.musicxml import parse_musicxml


FIXTURE = Path(__file__).parent / "fixtures" / "simple_melody.musicxml"
MINOR_FIXTURE = Path(__file__).parent / "fixtures" / "minor_melody.musicxml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source_22(n_measures: int = 1) -> SourceScore:
    """Minimal SourceScore in 2/2 time.  beat = Fraction(4, 2) = 2 quarter-note units.
    Quarter notes at position 1, 3 … within a measure are sub-beat (not strong)."""
    measures = [
        Measure(
            index=i,
            number=str(i + 1),
            start=Fraction(i * 4),
            duration=Fraction(4),
            beats=2,
            beat_type=2,
        )
        for i in range(n_measures)
    ]
    return SourceScore(title="test", parts=[], measures=measures, fifths=0, mode="major")


def _ev(id_: str, step: str, octave: int, start: Fraction, dur: Fraction) -> Event:
    return Event(
        id=id_,
        part_id="P1",
        measure_index=0,
        start=start,
        duration=dur,
        pitch=Pitch(step, octave),
    )


# ---------------------------------------------------------------------------
# Key signatures → tonic pitch class
# ---------------------------------------------------------------------------

class TestKeySignature:
    """tonic_from_signature must match the circle-of-fifths table."""

    @pytest.mark.parametrize("fifths,mode,expected", [
        (0,  "major", 0),   # C major
        (1,  "major", 7),   # G major
        (2,  "major", 2),   # D major
        (3,  "major", 9),   # A major
        (-1, "major", 5),   # F major
        (-2, "major", 10),  # Bb major
        (-3, "major", 3),   # Eb major
        (0,  "minor", 9),   # A minor (relative of C)
        (1,  "minor", 4),   # E minor (relative of G)
        (2,  "minor", 11),  # B minor (relative of D)
        (-1, "minor", 2),   # D minor (relative of F)
        (-2, "minor", 7),   # G minor (relative of Bb)
    ])
    def test_tonic(self, fifths: int, mode: str, expected: int) -> None:
        assert tonic_from_signature(fifths, mode) == expected


# ---------------------------------------------------------------------------
# Chord vocabulary: diatonic triads + chromatic extensions
# ---------------------------------------------------------------------------

class TestChordVocabularyCMajor:
    """_chord_vocabulary(0, 'major') must produce correct pitch-class sets."""

    def setup_method(self) -> None:
        self.vocab = _chord_vocabulary(0, "major")

    def test_seven_diatonic_plus_two_chromatic(self) -> None:
        assert len(self.vocab) == 9

    def test_i_c_major(self) -> None:
        root, pcs, quality = self.vocab[0]
        assert root == 0 and pcs == {0, 4, 7} and quality == "major"

    def test_ii_d_minor(self) -> None:
        root, pcs, quality = self.vocab[1]
        assert root == 2 and pcs == {2, 5, 9} and quality == "minor"

    def test_iii_e_minor(self) -> None:
        root, pcs, quality = self.vocab[2]
        assert root == 4 and pcs == {4, 7, 11} and quality == "minor"

    def test_iv_f_major(self) -> None:
        root, pcs, quality = self.vocab[3]
        assert root == 5 and pcs == {0, 5, 9} and quality == "major"

    def test_v_g_major_contains_leading_tone(self) -> None:
        root, pcs, quality = self.vocab[4]
        assert root == 7 and quality == "major"
        assert 11 in pcs  # B natural = leading tone in C major

    def test_vi_a_minor(self) -> None:
        root, pcs, quality = self.vocab[5]
        assert root == 9 and pcs == {0, 4, 9} and quality == "minor"

    def test_vii_diminished(self) -> None:
        root, pcs, quality = self.vocab[6]
        assert root == 11 and pcs == {2, 5, 11} and quality == "diminished"

    def test_secondary_dominant_vv_d_major(self) -> None:
        # V/V = D major: {D, F#, A} = {2, 6, 9}
        root, pcs, quality = self.vocab[7]
        assert root == 2 and quality == "major"
        assert 6 in pcs  # F# (raised third of D major)

    def test_borrowed_bvii_bb_major(self) -> None:
        # bVII borrowed from parallel minor = Bb major: {10, 2, 5}
        root, pcs, quality = self.vocab[8]
        assert root == 10 and quality == "major"
        assert pcs == {10, 2, 5}


class TestChordVocabularyAMinor:
    """_chord_vocabulary(9, 'minor') must use natural-minor scale degrees 1-7
    and append the harmonic-minor dominant (E major) as degree 9."""

    def setup_method(self) -> None:
        self.vocab = _chord_vocabulary(9, "minor")

    def test_i_a_minor(self) -> None:
        root, pcs, quality = self.vocab[0]
        assert root == 9 and quality == "minor"
        assert pcs == {9, 0, 4}  # A C E

    def test_ii_diminished(self) -> None:
        root, pcs, quality = self.vocab[1]
        assert root == 11 and quality == "diminished"  # B dim

    def test_v_natural_is_minor(self) -> None:
        # Natural-minor V = E minor {4, 7, 11}
        root, pcs, quality = self.vocab[4]
        assert root == 4 and quality == "minor"

    def test_harmonic_dominant_e_major(self) -> None:
        # Harmonic-minor V (degree 9) = E major {4, 8, 11} (G# raised)
        root, pcs, quality = self.vocab[8]
        assert root == 4 and quality == "major"
        assert 8 in pcs  # G# = raised 7th in A harmonic minor


class TestChordVocabularyGMajor:
    """Spot-check G major to confirm the root offsets work for non-zero tonic."""

    def test_v_of_g_is_d_major(self) -> None:
        vocab = _chord_vocabulary(7, "major")
        root, pcs, quality = vocab[4]
        assert root == 2 and quality == "major"  # D major
        assert 6 in pcs  # F# = leading tone in G major

    def test_vv_of_g_is_a_major(self) -> None:
        vocab = _chord_vocabulary(7, "major")
        root, pcs, quality = vocab[7]
        assert root == 9 and quality == "major"  # A major (V/V of G)
        assert 1 in pcs  # C# = raised third of A major


# ---------------------------------------------------------------------------
# Harmonic function transition weights
# ---------------------------------------------------------------------------

class TestHarmonicTransitions:
    """The functional hierarchy: T-PD-D-T.  Authentic cadence must score highest."""

    def test_authentic_cadence_v_to_i_is_strongest(self) -> None:
        # V→I beats any other progression resolving to tonic
        v_i = _transition(5, 1)
        assert v_i > _transition(4, 1)   # IV→I (plagal)
        assert v_i > _transition(2, 1)   # ii→I
        assert v_i > _transition(1, 1)   # I→I (repetition)

    def test_vii_to_i_is_positive(self) -> None:
        # Leading-tone chord resolves to tonic
        assert _transition(7, 1) > 0

    def test_predominant_to_dominant_is_positive(self) -> None:
        # ii→V and IV→V are grammatical motions
        assert _transition(2, 5) > 0
        assert _transition(4, 5) > 0

    def test_tonic_to_predominant_is_positive_or_neutral(self) -> None:
        # I→ii and I→IV begin a functional progression
        assert _transition(1, 2) >= -0.1
        assert _transition(1, 4) >= -0.1

    def test_secondary_dominant_resolves_to_dominant(self) -> None:
        # V/V→V is more natural than V/V→I (deceptive resolution)
        assert _transition(8, 5) > _transition(8, 1)

    def test_same_chord_repetition_weaker_than_resolution(self) -> None:
        # Staying on V is weaker than resolving V→I
        assert _transition(5, 1) > _transition(5, 5)

    def test_dominant_to_tonic_beats_dominant_to_predominant(self) -> None:
        # V resolves forward (to I), not backward (to IV/ii)
        assert _transition(5, 1) > _transition(5, 4)
        assert _transition(5, 1) > _transition(5, 2)

    def test_harmonic_or_borrowed_resolves_to_tonic(self) -> None:
        # degree 9 (harmonic-V or bVII) → I should be positive
        assert _transition(9, 1) > 0


# ---------------------------------------------------------------------------
# Structural melody reduction
# ---------------------------------------------------------------------------

class TestStructuralReduction:
    """Common-practice rule: passing and neighbor tones are ornamental.
    They must be eliminated by reduce_to_structural_melody when they appear
    on sub-beat positions with sub-beat durations in 2/2 time."""

    # In 2/2 time: beat = Fraction(4, 2) = 2 quarter-note units.
    # A quarter note at measure-position 1 or 3 has position % 2 != 0 → sub-beat.

    def _phrases(self, melody: list[Event]) -> list[Phrase]:
        start = melody[0].start
        end = melody[-1].end
        apex = max(ev.pitch.midi for ev in melody)
        return [Phrase("p1", start, end, apex, "open")]

    def test_stepwise_passing_tone_removed(self) -> None:
        # C – D – E in 2/2: D at position 1 (sub-beat), dur=1 < beat=2
        # C→D is +2, D→E is +2, same direction → stepwise passing tone
        source = _source_22(1)
        m = [
            _ev("n0", "C", 4, Fraction(0), Fraction(2)),  # beat 1, strong
            _ev("n1", "D", 4, Fraction(2), Fraction(1)),  # beat 2, strong (pos 2 % 2 == 0)
            _ev("n2", "E", 4, Fraction(3), Fraction(1)),  # AND-of-2, sub-beat
        ]
        # Rearrange: put D on sub-beat by shifting E to beat 2
        # True sub-beat passing tone: C(beat1, dur=1) D(AND-of-1, dur=1) E(beat2, dur=2)
        m = [
            _ev("n0", "C", 4, Fraction(0), Fraction(1)),  # pos=0, strong
            _ev("n1", "D", 4, Fraction(1), Fraction(1)),  # pos=1, sub-beat (1%2=1)
            _ev("n2", "E", 4, Fraction(2), Fraction(2)),  # pos=2, strong (beat 2)
        ]
        phrases = self._phrases(m)
        result = reduce_to_structural_melody(source, m, phrases, [])
        pitches = [ev.pitch.step for ev in result]
        assert "D" not in pitches, "sub-beat stepwise passing tone should be reduced"

    def test_upper_neighbor_tone_removed(self) -> None:
        # C – D – C pattern: D is upper neighbor at sub-beat position
        source = _source_22(1)
        m = [
            _ev("n0", "C", 4, Fraction(0), Fraction(1)),  # beat 1
            _ev("n1", "D", 4, Fraction(1), Fraction(1)),  # sub-beat
            _ev("n2", "C", 4, Fraction(2), Fraction(1)),  # beat 2
            _ev("n3", "E", 4, Fraction(3), Fraction(1)),  # sub-beat (kept as last)
        ]
        phrases = self._phrases(m)
        result = reduce_to_structural_melody(source, m, phrases, [])
        # n1 (D) should be gone: previous.pitch == following.pitch == C
        assert all(ev.id != "n1" for ev in result)

    def test_first_and_last_always_retained(self) -> None:
        # Boundary rule: the first and last events of the melody are never dropped
        source = _source_22(1)
        m = [
            _ev("n0", "C", 4, Fraction(0), Fraction(1)),
            _ev("n1", "D", 4, Fraction(1), Fraction(1)),
            _ev("n2", "E", 4, Fraction(2), Fraction(1)),
        ]
        phrases = self._phrases(m)
        result = reduce_to_structural_melody(source, m, phrases, [])
        assert result[0].id == "n0"
        assert result[-1].id == "n2"

    def test_strong_beat_note_retained_even_if_passing(self) -> None:
        # A note on a downbeat (position 0 or multiple of beat) is "strong"
        # and must survive regardless of its melodic role
        m = [
            _ev("n0", "A", 3, Fraction(0), Fraction(2)),  # beat 1, strong
            _ev("n1", "B", 3, Fraction(2), Fraction(2)),  # beat 2, strong (pos=2, 2%2=0)
            _ev("n2", "C", 4, Fraction(4), Fraction(2)),  # downbeat next measure
        ]
        # B is stepwise passing (A→B→C), but it IS on a strong beat → retained
        # Need 2 measures for this
        source2 = _source_22(2)
        phrases = self._phrases(m)
        result = reduce_to_structural_melody(source2, m, phrases, [])
        assert any(ev.id == "n1" for ev in result), "strong-beat note must be retained"

    def test_phrase_boundary_notes_retained(self) -> None:
        # The note AT a phrase boundary end time is always structural
        source = _source_22(2)
        m = [
            _ev("n0", "C", 4, Fraction(0), Fraction(1)),
            _ev("n1", "D", 4, Fraction(1), Fraction(1)),
            _ev("n2", "E", 4, Fraction(2), Fraction(4)),  # ends at t=6 = phrase boundary
            _ev("n3", "D", 4, Fraction(6), Fraction(1)),
            _ev("n4", "C", 4, Fraction(7), Fraction(1)),
        ]
        phrases = [
            Phrase("p1", Fraction(0), Fraction(6), 64, "open"),
            Phrase("p2", Fraction(6), Fraction(8), 62, "tonic-arrival"),
        ]
        result = reduce_to_structural_melody(source, m, phrases, [])
        # n2 ends exactly at phrase boundary t=6, so it must be kept
        assert any(ev.id == "n2" for ev in result)


# ---------------------------------------------------------------------------
# Phrase detection
# ---------------------------------------------------------------------------

class TestPhraseDetection:
    """detect_phrases splits at notation cues: rests/gaps, long notes, 4-bar groups."""

    def test_long_note_creates_boundary(self) -> None:
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        # simple_melody.musicxml has a half note in measure 2 (G4, dur=2)
        # This should trigger a phrase split somewhere
        assert len(song.phrases) >= 1

    def test_final_phrase_ends_at_melody_end(self) -> None:
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        melody_end = max(ev.end for ev in song.melody)
        assert song.phrases[-1].end == melody_end

    def test_tonic_arrival_cadence_detected(self) -> None:
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        # simple_melody ends on C5 (tonic in C major)
        assert song.phrases[-1].cadence == "tonic-arrival"

    def test_minor_key_tonic_arrival(self) -> None:
        source = parse_musicxml(MINOR_FIXTURE)
        song = analyze_score(source)
        # minor_melody ends on A3 (tonic in A minor, pc=9)
        assert song.phrases[-1].cadence == "tonic-arrival"
        assert song.tonic_pc == 9
        assert song.mode == "minor"

    def test_phrases_are_contiguous(self) -> None:
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        for left, right in zip(song.phrases, song.phrases[1:]):
            assert left.end <= right.start

    def test_apex_is_highest_note_in_phrase(self) -> None:
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        for phrase in song.phrases:
            notes = [
                ev for ev in song.melody
                if phrase.start <= ev.start < phrase.end and ev.pitch is not None
            ]
            if notes:
                assert phrase.apex_midi == max(ev.pitch.midi for ev in notes)


# ---------------------------------------------------------------------------
# Minor key analysis
# ---------------------------------------------------------------------------

class TestMinorKeyAnalysis:
    """analyze_score must detect A minor from the MusicXML key signature."""

    def test_tonic_is_a(self) -> None:
        source = parse_musicxml(MINOR_FIXTURE)
        song = analyze_score(source)
        assert song.tonic_pc == 9  # A

    def test_mode_is_minor(self) -> None:
        source = parse_musicxml(MINOR_FIXTURE)
        song = analyze_score(source)
        assert song.mode == "minor"

    def test_chord_vocabulary_includes_harmonic_dominant(self) -> None:
        # The harmonic analysis should have access to E major (V in harmonic minor)
        vocab = _chord_vocabulary(9, "minor")
        harmonic_v = vocab[8]
        root, pcs, quality = harmonic_v
        assert root == 4 and quality == "major"  # E major

    def test_minor_scale_tonal_pcs(self) -> None:
        # Natural A minor: A B C D E F G  = {9, 11, 0, 2, 4, 5, 7}
        source = parse_musicxml(MINOR_FIXTURE)
        song = analyze_score(source)
        # All melody notes should belong to A natural minor
        minor_pcs = {9, 11, 0, 2, 4, 5, 7}
        melody_pcs = {ev.pitch.midi % 12 for ev in song.melody if ev.pitch}
        assert melody_pcs <= minor_pcs


# ---------------------------------------------------------------------------
# Chord pitch classes carried on HarmonyCandidate
# ---------------------------------------------------------------------------

class TestCandidateChordPitchClasses:
    """A candidate carries the chord it names, so no consumer re-derives it.

    The triad qualities are checked against the C-major vocabulary, where the
    three triad types all occur: I is major, ii is minor, vii is diminished.
    """

    def _candidates(self) -> list[HarmonyCandidate]:
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        return [
            candidate
            for harmonic_slice in song.harmony
            for candidate in harmonic_slice.candidates
        ]

    def test_major_triad_pitch_classes(self) -> None:
        # C major (degree 1 of C major): C E G = {0, 4, 7}
        candidate = next(
            c for c in self._candidates() if c.degree == 1 and c.quality == "major"
        )
        assert candidate.pcs == frozenset({0, 4, 7})

    def test_minor_triad_pitch_classes(self) -> None:
        # D minor (degree 2 of C major): D F A = {2, 5, 9}
        candidate = next(
            c for c in self._candidates() if c.degree == 2 and c.quality == "minor"
        )
        assert candidate.pcs == frozenset({2, 5, 9})

    def test_diminished_triad_pitch_classes(self) -> None:
        # B diminished (degree 7 of C major): B D F = {11, 2, 5}
        candidate = next(
            c for c in self._candidates() if c.degree == 7 and c.quality == "diminished"
        )
        assert candidate.pcs == frozenset({11, 2, 5})

    def test_pcs_match_the_vocabulary_that_built_them(self) -> None:
        # Every degree's pcs is the set _chord_vocabulary computed, not a
        # re-expansion of root_pc + quality.
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        vocab = _chord_vocabulary(song.tonic_pc, song.mode)
        for harmonic_slice in song.harmony:
            for candidate in harmonic_slice.candidates:
                _, pcs, _ = vocab[candidate.degree - 1]
                assert candidate.pcs == frozenset(pcs)


class TestConsumersShareOneChordSet:
    """analysis's non-chord-tone test and counterpoint's chord-tone bonus read
    the same pitch classes for the same slice."""

    def test_analysis_and_counterpoint_read_the_same_set(self) -> None:
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        assert song.harmony, "fixture must analyse to at least one harmonic slice"
        for harmonic_slice in song.harmony:
            analysis_pcs = harmonic_slice.selected.pcs
            counterpoint_pcs, root = _harmony_context(song, harmonic_slice.start)
            assert counterpoint_pcs == analysis_pcs
            assert root == harmonic_slice.selected.root_pc

    def test_generation_reads_the_same_set(self) -> None:
        # _candidate_midis picks output pitches by chord membership; every midi
        # it offers must reduce to a pitch class in the candidate's own set.
        source = parse_musicxml(FIXTURE)
        song = analyze_score(source)
        profile = get_profile("cello")
        for harmonic_slice in song.harmony:
            midis = _candidate_midis(harmonic_slice, profile)
            assert midis, "profile must admit at least one chord tone"
            assert {midi % 12 for midi in midis} == set(harmonic_slice.selected.pcs)
