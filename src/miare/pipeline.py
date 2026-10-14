"""The analyse/arrange pipeline, with no opinion about where its input came from.

`cli.py` reaches this through paths on disk; `server.py` reaches it through bytes posted
by a browser. Both go through the same two functions, so a change in processing order or
in the report shape lands on both at once — the alternative, each caller sequencing
`parse_musicxml` → `analyze_score` → an arranger → `validate_*` for itself, is how the
server quietly becomes a second implementation that drifts from the CLI's output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__
from .analysis import analyze_score
from .counterpoint import arrange_countermelody
from .generation import arrange_support, validate_arrangement
from .ir import ArrangementIR, SourceScore
from .musicxml import parse_musicxml, validate_round_trip, write_arrangement


IMPLEMENTED_RELATIONSHIPS = ("support", "countermelody")


def inspect_source(score: SourceScore) -> dict[str, Any]:
    """The `source` block of every report, and what `miare inspect` prints."""
    total_notes = sum(
        1 for part in score.parts for event in part.events if event.pitch is not None
    )
    ranges = {}
    for part in score.parts:
        pitches = [event.pitch.midi for event in part.events if event.pitch is not None]
        ranges[part.id] = {
            "name": part.name,
            "notes": len(pitches),
            "minimum_midi": min(pitches) if pitches else None,
            "maximum_midi": max(pitches) if pitches else None,
            "voices": sorted({event.voice for event in part.events}),
        }
    return {
        "title": score.title,
        "parts": len(score.parts),
        "measures": len(score.measures),
        "notes": total_notes,
        "duration_quarters": str(score.measures[-1].end),
        "key_signature": {"fifths": score.fifths, "mode": score.mode},
        "part_details": ranges,
        "diagnostics": score.diagnostics,
    }


def analyze_source(xml_path: str | Path) -> dict[str, Any]:
    """What `miare analyze` writes: the inspected source plus its analysis."""
    score = parse_musicxml(xml_path)
    return {
        "source": inspect_source(score),
        "analysis": analyze_score(score),
    }


def arrange_source(
    xml_input: str | Path,
    xml_output: Path,
    *,
    relationship: str = "support",
    voice1_profile: str = "violin",
    voice2_profile: str = "cello",
    seed: int = 1,
    beam_width: int = 32,
    context_measures: int = 2,
    allow_lossy_input: bool = False,
    input_label: str | None = None,
    output_label: str | None = None,
) -> dict[str, Any]:
    """Arrange `xml_input`, write the score to `xml_output`, return the report.

    `input_label` and `output_label` are what the report records as `input` and `output`.
    They default to the two paths, which is what the CLI wants; the server passes the
    names a browser knows, because its own paths are in a temp directory that is gone by
    the time anyone reads the report.
    """
    if relationship not in IMPLEMENTED_RELATIONSHIPS:
        raise ValueError(
            f"relationship {relationship!r} is represented by the design but not implemented; "
            "implemented relationships are 'support' and 'countermelody'"
        )
    score = parse_musicxml(xml_input)
    unsupported = [
        diagnostic for diagnostic in score.diagnostics
        if diagnostic.startswith("UNSUPPORTED-OUTPUT:")
    ]
    if unsupported and not allow_lossy_input:
        raise ValueError(
            "input contains notation that cannot yet be preserved: "
            + "; ".join(unsupported)
            + ". Pass --allow-lossy-input to proceed explicitly"
        )
    song = analyze_score(score)
    if relationship == "support":
        arrangement = arrange_support(
            score,
            song,
            voice1_profile=voice1_profile,
            voice2_profile=voice2_profile,
            seed=seed,
        )
    else:
        arrangement = arrange_countermelody(
            score,
            song,
            voice1_profile=voice1_profile,
            voice2_profile=voice2_profile,
            beam_width=beam_width,
            context_measures=context_measures,
            seed=seed,
        )
    hard_errors = validate_arrangement(arrangement)
    if hard_errors:
        raise ValueError(
            "arrangement violates hard constraints: " + "; ".join(hard_errors)
        )
    _write_and_validate(xml_output, score, arrangement)
    return {
        "miare_version": __version__,
        "input": str(input_label if input_label is not None else xml_input),
        "output": str(output_label if output_label is not None else xml_output),
        "seed": seed,
        "source": inspect_source(score),
        "analysis": song,
        "arrangement": arrangement,
        "round_trip": {"valid": True, "errors": []},
    }


def _write_and_validate(
    xml_output: Path, score: SourceScore, arrangement: ArrangementIR
) -> None:
    write_arrangement(xml_output, score, arrangement)
    round_trip_errors = validate_round_trip(
        xml_output,
        score.measures,
        expected_parts=2,
        expected_arrangement=arrangement,
    )
    if round_trip_errors:
        xml_output.unlink(missing_ok=True)
        raise ValueError(
            "generated MusicXML failed round-trip validation: "
            + "; ".join(round_trip_errors)
        )
