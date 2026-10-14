"""The bundled samples score_viewer.html serves.

The viewer is a static page with no Python in it, so what these tests guard is the
contract between the two: the committed sample set is regenerable from its sources,
and each sample holds the files the page goes looking for.
"""

from __future__ import annotations

import json
from pathlib import Path

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
