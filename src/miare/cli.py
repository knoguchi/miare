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
from .instruments import PROFILES
from .ir import to_jsonable
from .musicxml import parse_musicxml
from .pipeline import analyze_source, arrange_source, inspect_source
from .server import serve


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


def _cmd_analyze(args: argparse.Namespace) -> int:
    with _as_musicxml_input(Path(args.input)) as xml_path:
        _dump_json(analyze_source(xml_path), args.output)
    return 0


def _cmd_arrange(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    with _as_musicxml_input(Path(args.input)) as xml_input:
        with _as_musicxml_output(output_path) as xml_output:
            report = arrange_source(
                xml_input,
                xml_output,
                relationship=args.relationship,
                voice1_profile=args.voice1_profile,
                voice2_profile=args.voice2_profile,
                seed=args.seed,
                beam_width=args.beam_width,
                context_measures=args.context_measures,
                allow_lossy_input=args.allow_lossy_input,
                # The report names the paths the user typed, not the temp MusicXML a
                # .mscz conversion routes through.
                input_label=Path(args.input),
                output_label=output_path,
            )
        report_path = args.report
        if report_path is None:
            report_path = output_path.with_suffix(".miare.json")
        _dump_json(report, report_path)
    print(f"wrote {output_path}")
    print(f"wrote {report_path}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    return serve(root=Path(args.root), port=args.port)


def _cmd_inspect(args: argparse.Namespace) -> int:
    with _as_musicxml_input(Path(args.input)) as xml_path:
        data = inspect_source(parse_musicxml(xml_path))
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

    serve_parser = subparsers.add_parser(
        "serve",
        help="serve score_viewer.html and the analyze/arrange API on localhost",
    )
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument(
        "--root", default=".", help="directory served statically (default: cwd)"
    )
    serve_parser.set_defaults(func=_cmd_serve)
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
