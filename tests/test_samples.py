"""The bundled samples score_viewer.html serves.

The viewer is a static page with no Python in it, so what these tests guard is the
contract between the two: the committed sample set is regenerable from its sources,
and each sample holds the files the page goes looking for.
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from miare.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "samples"
VIEWER = REPO_ROOT / "score_viewer.html"


def sample_dirs() -> list[Path]:
    return sorted(path for path in SAMPLES.iterdir() if path.is_dir())


def test_at_least_two_samples_are_committed() -> None:
    assert len(sample_dirs()) >= 2


@pytest.mark.parametrize("sample", sample_dirs(), ids=lambda path: path.name)
def test_sample_holds_a_source_an_arrangement_and_its_report(sample: Path) -> None:
    source = sample / "source.musicxml"
    assert source.is_file(), f"{sample.name} has no source.musicxml"

    arrangements = [
        path for path in sorted(sample.glob("*.musicxml")) if path.name != "source.musicxml"
    ]
    assert arrangements, f"{sample.name} has no arrangement beside its source"

    for arrangement in arrangements:
        report = arrangement.with_suffix(".miare.json")
        assert report.is_file(), f"{arrangement.name} has no {report.name}"
        data = json.loads(report.read_text(encoding="utf-8"))
        # The viewer reads exactly these: phrase boundaries, and the measure count it
        # matches a report to a score by.
        assert data["analysis"]["phrases"], f"{report.name} reports no phrases"
        assert data["source"]["measures"] > 0
        assert data["round_trip"]["valid"] is True


@pytest.mark.parametrize("sample", sample_dirs(), ids=lambda path: path.name)
def test_report_names_the_two_scores_the_viewer_pairs_it_with(sample: Path) -> None:
    """A report has to name its own source and arrangement by file name.

    `score_viewer.html` attaches a `.miare.json` to a deck by the base name in `input`
    and `output`, checked against `source.measures`. When no deck matches it falls back
    to whichever score was opened last, so a report that names something else does not
    fail loudly — it draws the source's phrase boundaries over the wrong score. Every
    sample calls its source `source.musicxml`, which is why the measure count is part of
    the match and is asserted here too.
    """
    for arrangement in sorted(sample.glob("*.musicxml")):
        if arrangement.name == "source.musicxml":
            continue
        report = arrangement.with_suffix(".miare.json")
        data = json.loads(report.read_text(encoding="utf-8"))

        assert Path(data["input"]).name == "source.musicxml", (
            f"{report.name} names input {data['input']!r}; the viewer looks for the "
            "base name of a score it has open"
        )
        assert Path(data["output"]).name == arrangement.name, (
            f"{report.name} names output {data['output']!r}, not {arrangement.name}"
        )

        source_measures = _measure_count(sample / "source.musicxml")
        assert data["source"]["measures"] == source_measures, (
            f"{report.name} says {data['source']['measures']} source measures, "
            f"source.musicxml has {source_measures}; the viewer matches on this count"
        )


@pytest.mark.parametrize("sample", sample_dirs(), ids=lambda path: path.name)
def test_report_phrase_boundaries_land_inside_the_source(sample: Path) -> None:
    """Every phrase start must be a time the source actually reaches.

    The viewer resolves a phrase start to a measure of the score the report calls
    `input`, then draws it on that measure of each deck. A start past the end of the
    source clamps to the last measure, so a wrong offset shows up as boundaries piled on
    the final bar rather than as an error.
    """
    for arrangement in sorted(sample.glob("*.musicxml")):
        if arrangement.name == "source.musicxml":
            continue
        report = arrangement.with_suffix(".miare.json")
        data = json.loads(report.read_text(encoding="utf-8"))
        phrases = data["analysis"]["phrases"]
        total = _total_quarters(sample / "source.musicxml")

        starts = [_quarters(phrase["start"]) for phrase in phrases]
        assert starts == sorted(starts), f"{report.name} lists phrases out of order"
        for start in starts:
            assert 0 <= start < total, (
                f"{report.name} starts a phrase at quarter {start}; "
                f"source.musicxml is {total} quarters long"
            )


def _quarters(value: object) -> float:
    """Phrase times are a number or a fraction string ("37/2"), as the viewer reads them."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        return float(numerator) / float(denominator)
    return float(text)


def _measure_count(score: Path) -> int:
    root = ElementTree.parse(score).getroot()
    part = root.find("part")
    assert part is not None, f"{score.name} has no <part>"
    return len(part.findall("measure"))


def _total_quarters(score: Path) -> float:
    """Length of the first part, counting the way the viewer's parser does."""
    root = ElementTree.parse(score).getroot()
    part = root.find("part")
    assert part is not None, f"{score.name} has no <part>"
    divisions, total = 1, 0.0
    for measure in part.findall("measure"):
        found = measure.find("attributes/divisions")
        if found is not None and found.text:
            divisions = int(found.text)
        for note in measure.findall("note"):
            if note.find("chord") is not None:
                continue
            duration = note.find("duration")
            if duration is not None and duration.text:
                total += int(duration.text) / divisions
    return total


@pytest.mark.parametrize("sample", sample_dirs(), ids=lambda path: path.name)
def test_sample_arrangements_regenerate_byte_for_byte(sample: Path, tmp_path: Path) -> None:
    """`scripts/make-samples.sh` must reproduce what is committed.

    Arrangement is deterministic given a source and a seed, so a committed sample that
    no longer regenerates means either the arranger changed or the sample is stale. The
    viewer would then show output no current command produces.
    """
    source = sample / "source.musicxml"
    for arrangement in sorted(sample.glob("*.musicxml")):
        if arrangement.name == "source.musicxml":
            continue
        relationship = arrangement.stem
        fresh = tmp_path / f"{sample.name}-{relationship}.musicxml"
        main(
            [
                "arrange",
                str(source),
                "-o",
                str(fresh),
                "--relationship",
                relationship,
                "--voice1-profile",
                _profile(arrangement, 0),
                "--voice2-profile",
                _profile(arrangement, 1),
                "--beam-width",
                "32",
                "--seed",
                "1",
            ]
        )
        assert fresh.read_text(encoding="utf-8") == arrangement.read_text(encoding="utf-8"), (
            f"{arrangement.relative_to(REPO_ROOT)} is stale — "
            "re-run ./scripts/make-samples.sh and commit the result"
        )


def _profile(arrangement: Path, index: int) -> str:
    """The instrument the committed report says that voice was arranged for."""
    report = json.loads(arrangement.with_suffix(".miare.json").read_text(encoding="utf-8"))
    return report["arrangement"]["voices"][index]["profile"]["id"]


def test_viewer_is_one_self_contained_page_at_the_repo_root() -> None:
    assert VIEWER.is_file()
    text = VIEWER.read_text(encoding="utf-8")
    # One CDN script tag and nothing else to fetch or build: no bundler, no node_modules.
    assert text.count("<script src=") == 1
    assert "opensheetmusicdisplay" in text
    assert "samples/" in text, "the page must list the bundled samples"
