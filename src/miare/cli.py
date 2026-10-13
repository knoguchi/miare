from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
import sys
from typing import Any

from . import __version__
from .analysis import analyze_score
from .counterpoint import arrange_countermelody
from .generation import arrange_support, validate_arrangement
from .instruments import PROFILES
from .ir import SourceScore, to_jsonable
from .musicxml import parse_musicxml, validate_round_trip, write_arrangement


MUSESCORE_FORMATS = frozenset({".mscz", ".mscx"})


def _dump_json(data: Any, output: str | Path | None) -> None:
    text = json.dumps(to_jsonable(data), ensure_ascii=False, indent=2) + "\n"
    if output is None or str(output) == "-":
        sys.stdout.write(text)
    else:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _find_mscore() -> str | None:
    for name in ("mscore", "mscore4", "musescore"):
        if shutil.which(name):
            return name
    for bundle in (
        "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
        "/Applications/MuseScore 3.app/Contents/MacOS/mscore",
    ):
        if Path(bundle).exists():
            return bundle
    return None


def _convert_via_mscore(src: Path, dst: Path) -> None:
    """Convert between any two formats that MuseScore supports (mscz ↔ musicxml, etc.)."""
    mscore = _find_mscore()
    if mscore is None:
        raise ValueError(
            "MuseScore CLI not found — install MuseScore 4 and ensure "
            "'mscore' is on PATH to convert .mscz/.mscx files"
        )
    result = subprocess.run(
        [mscore, "-o", str(dst), str(src)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dst.exists():
        err = (result.stderr or result.stdout).strip()[:400]
        raise ValueError(
            f"MuseScore conversion failed (exit {result.returncode}): {err}"
        )


@contextlib.contextmanager
def _as_musicxml_input(path: Path):
    """Yield a MusicXML path for reading.  If path is .mscz/.mscx, convert first."""
    if path.suffix.lower() not in MUSESCORE_FORMATS:
        yield path
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        xml_path = Path(tmpdir) / (path.stem + ".musicxml")
        print(f"converting {path.name} → MusicXML via MuseScore…")
        _convert_via_mscore(path, xml_path)
        yield xml_path


@contextlib.contextmanager
def _as_musicxml_output(path: Path):
    """Yield the actual MusicXML write path.  If path is .mscz/.mscx, write to a
    temp MusicXML first and convert to the requested format at context exit."""
    if path.suffix.lower() not in MUSESCORE_FORMATS:
        yield path
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        xml_path = Path(tmpdir) / (path.stem + ".musicxml")
        yield xml_path
        print(f"converting MusicXML → {path.name} via MuseScore…")
        _convert_via_mscore(xml_path, path)


def _inspect(score: SourceScore) -> dict[str, Any]:
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


def _cmd_analyze(args: argparse.Namespace) -> int:
    with _as_musicxml_input(Path(args.input)) as xml_path:
        score = parse_musicxml(xml_path)
        song = analyze_score(score)
        _dump_json(
            {
                "source": _inspect(score),
                "analysis": song,
            },
            args.output,
        )
    return 0


def _cmd_arrange(args: argparse.Namespace) -> int:
    if args.relationship not in {"support", "countermelody"}:
        raise ValueError(
            f"relationship {args.relationship!r} is represented by the design but not implemented; "
            "implemented relationships are 'support' and 'countermelody'"
        )
    output_path = Path(args.output)
    with _as_musicxml_input(Path(args.input)) as xml_input:
        score = parse_musicxml(xml_input)
        unsupported = [
            diagnostic for diagnostic in score.diagnostics
            if diagnostic.startswith("UNSUPPORTED-OUTPUT:")
        ]
        if unsupported and not args.allow_lossy_input:
            raise ValueError(
                "input contains notation that cannot yet be preserved: "
                + "; ".join(unsupported)
                + ". Pass --allow-lossy-input to proceed explicitly"
            )
        song = analyze_score(score)
        if args.relationship == "support":
            arrangement = arrange_support(
                score,
                song,
                voice1_profile=args.voice1_profile,
                voice2_profile=args.voice2_profile,
                seed=args.seed,
            )
        else:
            arrangement = arrange_countermelody(
                score,
                song,
                voice1_profile=args.voice1_profile,
                voice2_profile=args.voice2_profile,
                beam_width=args.beam_width,
                context_measures=args.context_measures,
                seed=args.seed,
            )
        hard_errors = validate_arrangement(arrangement)
        if hard_errors:
            raise ValueError(
                "arrangement violates hard constraints: " + "; ".join(hard_errors)
            )
        with _as_musicxml_output(output_path) as xml_output:
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
        report_path = args.report
        if report_path is None:
            report_path = output_path.with_suffix(".miare.json")
        _dump_json(
            {
                "miare_version": __version__,
                "input": str(Path(args.input)),
                "output": str(output_path),
                "seed": args.seed,
                "source": _inspect(score),
                "analysis": song,
                "arrangement": arrangement,
                "round_trip": {"valid": True, "errors": []},
            },
            report_path,
        )
    print(f"wrote {output_path}")
    print(f"wrote {report_path}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    with _as_musicxml_input(Path(args.input)) as xml_path:
        score = parse_musicxml(xml_path)
        data = _inspect(score)
    if args.json:
        _dump_json(data, None)
    else:
        print(f"title: {data['title']}")
        print(f"parts: {data['parts']}")
        print(f"measures: {data['measures']}")
        print(f"notes: {data['notes']}")
        print(f"duration (quarters): {data['duration_quarters']}")
        for part_id, details in data["part_details"].items():
            print(
                f"{part_id} {details['name']}: {details['notes']} notes, "
                f"MIDI {details['minimum_midi']}..{details['maximum_midi']}, "
                f"voices={','.join(details['voices'])}"
            )
        for diagnostic in data["diagnostics"]:
            print(f"warning: {diagnostic}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="miare",
        description="Arrange a completed MusicXML score using its own musical language.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="analyze a completed MusicXML score")
    analyze.add_argument("input")
    analyze.add_argument("-o", "--output", default="-")
    analyze.set_defaults(func=_cmd_analyze)

    arrange = subparsers.add_parser("arrange", help="create a two-voice arrangement")
    arrange.add_argument("input")
    arrange.add_argument("-o", "--output", required=True)
    arrange.add_argument(
        "--voice1-profile", default="violin", choices=sorted(PROFILES)
    )
    arrange.add_argument(
        "--voice2-profile", default="cello", choices=sorted(PROFILES)
    )
    arrange.add_argument(
        "--relationship",
        default="support",
        choices=("support", "countermelody", "dialogue", "imitation", "equal", "auto"),
    )
    arrange.add_argument("--seed", type=int, default=1)
    arrange.add_argument(
        "--beam-width",
        type=int,
        default=32,
        help="countermelody search width",
    )
    arrange.add_argument(
        "--context-measures",
        type=int,
        default=2,
        help="past and future context window for countermelody generation",
    )
    arrange.add_argument(
        "--allow-lossy-input",
        action="store_true",
        help="explicitly allow listed source notation to be omitted from output",
    )
    arrange.add_argument(
        "--report",
        help="analysis/provenance JSON path; defaults to OUTPUT.miare.json",
    )
    arrange.set_defaults(func=_cmd_arrange)

    inspect = subparsers.add_parser("inspect", help="inspect score structure")
    inspect.add_argument("input")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=_cmd_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
