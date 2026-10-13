from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

from miare.analysis import analyze_score
from miare.cli import main
from miare.counterpoint import arrange_countermelody
from miare.generation import arrange_support, validate_arrangement
from miare.instruments import generated_from_source
from miare.ir import Event, Pitch
from miare.musicxml import (
    _duration_notation,
    parse_musicxml,
    validate_round_trip,
    write_arrangement,
)


FIXTURE = Path(__file__).parent / "fixtures" / "simple_melody.musicxml"


def test_analysis_extracts_song_language() -> None:
    source = parse_musicxml(FIXTURE)
    song = analyze_score(source)

    assert source.title == "Simple Melody"
    assert len(source.measures) == 4
    assert song.tonic_pc == 0
    assert song.mode == "major"
    assert song.melody_part_id == "P1"
    assert len(song.melody) == 12
    assert song.phrases[-1].cadence == "tonic-arrival"
    assert len(song.harmony) == 8
    assert song.musical_language.rhythmic_vocabulary["1"] == 10
    assert "bassIdioms" in song.musical_language.unknown_fields


def test_support_arrangement_round_trips(tmp_path: Path) -> None:
    source = parse_musicxml(FIXTURE)
    song = analyze_score(source)
    arrangement = arrange_support(source, song, seed=7)
    output = tmp_path / "arranged.musicxml"

    assert arrangement.relationship == "support"
    assert len(arrangement.voices) == 2
    assert arrangement.voices[1].events
    assert not validate_arrangement(arrangement)

    write_arrangement(output, source, arrangement)
    assert not validate_round_trip(
        output,
        source.measures,
        expected_parts=2,
        expected_arrangement=arrangement,
    )
    parsed = parse_musicxml(output)
    assert len(parsed.parts) == 2
    assert all(part.events for part in parsed.parts)


def test_cli_writes_score_and_provenance(tmp_path: Path) -> None:
    output = tmp_path / "cli-output.musicxml"
    report = tmp_path / "report.json"

    status = main(
        [
            "arrange",
            str(FIXTURE),
            "-o",
            str(output),
            "--report",
            str(report),
            "--seed",
            "11",
        ]
    )

    assert status == 0
    assert output.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["round_trip"]["valid"] is True
    assert payload["analysis"]["musical_language"]["evidence"]
    assert payload["arrangement"]["provenance"]


def test_same_seed_is_reproducible() -> None:
    source = parse_musicxml(FIXTURE)
    song = analyze_score(source)
    first = arrange_support(source, song, seed=42)
    second = arrange_support(source, song, seed=42)

    assert [
        event.pitch.midi for event in first.voices[1].events
    ] == [
        event.pitch.midi for event in second.voices[1].events
    ]


def test_triplet_durations_have_musicxml_time_modification() -> None:
    assert _duration_notation(Fraction(1, 6)) == ("16th", 0, (3, 2))
    assert _duration_notation(Fraction(1, 3)) == ("eighth", 0, (3, 2))


def test_countermelody_uses_beam_plan_and_round_trips(tmp_path: Path) -> None:
    source = parse_musicxml(FIXTURE)
    song = analyze_score(source)
    arrangement = arrange_countermelody(
        source,
        song,
        beam_width=24,
        context_measures=2,
        seed=5,
    )
    output = tmp_path / "countermelody.musicxml"

    assert arrangement.relationship == "countermelody"
    assert not validate_arrangement(arrangement)
    assert arrangement.provenance[0]["kind"] == "counterpoint-plan"
    assert arrangement.provenance[0]["candidate_summary"]
    assert arrangement.provenance[0]["plan"]["spans"][0]["identity_policy"] == "protected"
    assert (
        "source_identity"
        in arrangement.provenance[0]["selected_critic_breakdown"]
    )
    counterline = arrangement.voices[1].events
    assert counterline
    assert all(event.role == "countermelody" for event in counterline)
    # With no chromatic source context, the generated line stays in the
    # source key rather than inventing chromatic passing chains.
    assert {event.pitch.midi % 12 for event in counterline} <= {0, 2, 4, 5, 7, 9, 11}

    write_arrangement(output, source, arrangement)
    assert not validate_round_trip(
        output,
        source.measures,
        expected_parts=2,
        expected_arrangement=arrangement,
    )


def test_register_mapping_preserves_phrase_contour() -> None:
    events = [
        Event(
            id="n1",
            part_id="P1",
            measure_index=0,
            start=Fraction(0),
            duration=Fraction(1),
            pitch=Pitch("F", 3, 1),
        ),
        Event(
            id="n2",
            part_id="P1",
            measure_index=0,
            start=Fraction(1),
            duration=Fraction(1),
            pitch=Pitch("G", 3),
        ),
    ]
    generated, diagnostics = generated_from_source(
        events,
        profile_min=55,
        profile_max=103,
        prefer_flats=False,
    )

    assert [event.pitch.midi for event in generated] == [66, 67]
    assert diagnostics
