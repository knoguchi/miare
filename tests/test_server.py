"""`miare serve` — the viewer's API.

Driven over real HTTP with `urllib`, not by calling the handler's functions, because the
contract the browser depends on is the wire one: the status code, the JSON shape, and the
fact that a bad score comes back as `{"error": ...}` instead of an HTML traceback.

The test that matters most is `test_arrange_matches_the_cli_for_the_same_seed`: the server
exists to put the CLI's pipeline behind a socket, and if its output ever diverges from
`miare arrange` it has become a second arranger.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree

import pytest

from miare.cli import main
from miare.server import make_server

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "simple_melody.musicxml"


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    """One server on an ephemeral port for the module, serving the repo root."""
    httpd = make_server(REPO_ROOT, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def post(url: str, body: bytes, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    """Status and decoded JSON, for the error responses too — urllib raises on 4xx."""
    request = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, raw.decode("utf-8", "replace")


def test_health_reports_the_api_is_there(base_url: str) -> None:
    """The one endpoint the viewer probes to decide whether to offer Arrange."""
    with urllib.request.urlopen(f"{base_url}/api/health", timeout=10) as response:
        assert response.status == 200
        payload = json.loads(response.read())
    assert payload["ok"] is True
    assert "countermelody" in payload["relationships"]


def test_analyze_returns_the_analysis_the_viewer_draws_from(base_url: str) -> None:
    status, payload = post(f"{base_url}/api/analyze", FIXTURE.read_bytes())
    assert status == 200
    # The two blocks score_viewer.html reads: Roman numerals, and phrase markers.
    assert payload["analysis"]["harmony"]
    assert payload["analysis"]["phrases"]
    assert payload["source"]["measures"] > 0


def test_arrange_returns_a_two_part_score_and_its_report(base_url: str) -> None:
    status, payload = post(
        f"{base_url}/api/arrange?relationship=countermelody&seed=1", FIXTURE.read_bytes()
    )
    assert status == 200
    root = ElementTree.fromstring(payload["musicxml"])
    assert len(root.findall("part")) == 2
    assert len(payload["report"]["arrangement"]["voices"]) == 2
    # Same report shape as a committed .miare.json, which is what makes the viewer's
    # harmony and phrase overlays light up for a freshly arranged score.
    assert payload["report"]["round_trip"]["valid"] is True
    assert payload["report"]["analysis"]["harmony"]
    assert payload["report"]["analysis"]["phrases"]


@pytest.mark.parametrize("relationship", ["support", "countermelody"])
def test_arrange_matches_the_cli_for_the_same_seed(
    base_url: str, tmp_path: Path, relationship: str
) -> None:
    """The server must be the CLI's pipeline, not a second implementation.

    Arrangement is deterministic given a source and a seed, so identical options have to
    produce byte-identical MusicXML through either entry point. A divergence here means
    the two paths have drifted apart.
    """
    expected = tmp_path / f"{relationship}.musicxml"
    main(
        [
            "arrange",
            str(FIXTURE),
            "-o",
            str(expected),
            "--relationship",
            relationship,
            "--seed",
            "7",
            "--beam-width",
            "32",
        ]
    )
    status, payload = post(
        f"{base_url}/api/arrange?relationship={relationship}&seed=7&beam_width=32",
        FIXTURE.read_bytes(),
    )
    assert status == 200
    assert payload["musicxml"] == expected.read_text(encoding="utf-8")

    cli_report = json.loads(expected.with_suffix(".miare.json").read_text(encoding="utf-8"))
    served = payload["report"]
    # `input`/`output` name paths, which legitimately differ: the CLI records what was
    # typed, the server what the browser called the file. Everything musical must match.
    for block in ("analysis", "arrangement", "source", "seed", "round_trip"):
        assert served[block] == cli_report[block], f"{block} differs from the CLI's report"


def test_malformed_body_is_a_json_error_not_a_traceback(base_url: str) -> None:
    status, payload = post(f"{base_url}/api/analyze", b"this is not MusicXML at all")
    assert status == 400
    assert isinstance(payload, dict), f"expected a JSON body, got {payload!r}"
    assert payload["error"]
    assert "Traceback" not in payload["error"]


def test_the_report_names_the_score_the_browser_sent(base_url: str) -> None:
    """`input`/`output` are what the viewer titles its diagnostics with.

    The server's own paths are a temp directory that is gone by the time anyone reads
    the report, so it records the name the browser supplied instead.
    """
    status, payload = post(
        f"{base_url}/api/arrange?relationship=support&seed=1&name=tune.musicxml",
        FIXTURE.read_bytes(),
    )
    assert status == 200
    report = payload["report"]
    assert report["input"] == "tune.musicxml"
    assert report["output"] == "tune.support.musicxml"
    assert "/" not in report["output"] and tempfile.gettempdir() not in report["output"]


def test_a_posted_name_cannot_escape_into_a_path(base_url: str) -> None:
    """A name is a label on the report, never something the server opens."""
    status, payload = post(
        f"{base_url}/api/arrange?relationship=support&name=" + "../../etc/passwd",
        FIXTURE.read_bytes(),
    )
    assert status == 200
    assert payload["report"]["input"] == "passwd"


def test_an_error_does_not_name_the_server_s_temp_directory(base_url: str) -> None:
    """The caller posted bytes, not a path, so a scratch path means nothing to them."""
    status, payload = post(f"{base_url}/api/analyze", b"<score-partwise")
    assert status == 400
    assert "miare-serve-" not in payload["error"], payload["error"]
    assert tempfile.gettempdir() not in payload["error"], payload["error"]


def test_an_unimplemented_relationship_is_a_400(base_url: str) -> None:
    status, payload = post(
        f"{base_url}/api/arrange?relationship=dialogue", FIXTURE.read_bytes()
    )
    assert status == 400
    assert "dialogue" in payload["error"]


def test_empty_body_is_a_400(base_url: str) -> None:
    status, payload = post(f"{base_url}/api/analyze", b"")
    assert status == 400
    assert payload["error"]


def test_an_oversized_score_is_refused_before_it_is_parsed(base_url: str) -> None:
    """Declared length alone is enough — the parser never sees the bytes."""
    from miare.server import MAX_BODY_BYTES

    status, payload = post(
        f"{base_url}/api/analyze", b"x" * (MAX_BODY_BYTES + 1)
    )
    assert status == 413
    assert payload["error"]


def test_a_cross_origin_caller_is_refused(base_url: str) -> None:
    """Another page the user has open must not be able to drive the arranger."""
    status, payload = post(
        f"{base_url}/api/analyze",
        FIXTURE.read_bytes(),
        {"Origin": "https://example.com"},
    )
    assert status == 403
    assert "example.com" in payload["error"]


def test_a_localhost_origin_is_allowed(base_url: str) -> None:
    status, _ = post(
        f"{base_url}/api/analyze",
        FIXTURE.read_bytes(),
        {"Origin": base_url},
    )
    assert status == 200


def test_unknown_post_route_is_a_json_404(base_url: str) -> None:
    status, payload = post(f"{base_url}/api/nope", b"{}")
    assert status == 404
    assert payload["error"]


def test_the_viewer_is_served_with_the_api_marker(base_url: str) -> None:
    """How the page knows to offer Arrange.

    It reads a meta tag rather than fetching /api/health, because a failed probe would
    print a console error under a plain static server — where the page is working as
    designed. The tag is added on the way out, so the committed file stays clean.
    """
    with urllib.request.urlopen(f"{base_url}/score_viewer.html", timeout=10) as response:
        html = response.read().decode("utf-8")
    marker = '<meta name="miare-api" content="1">'
    head = html[: html.index("</head>")]
    assert marker in head, "the page must be able to see the tag before its script runs"
    assert head.count(marker) == 1, "injected once, not on every pass"

    on_disk = (REPO_ROOT / "score_viewer.html").read_text(encoding="utf-8")
    head_on_disk = on_disk[: on_disk.index("</head>")]
    assert marker not in head_on_disk, (
        "the marker belongs to the served response, not the committed page — "
        "with it on disk a static server would offer an Arrange button that cannot work"
    )
    # And the page reads that tag rather than probing, so nothing 404s on load.
    assert 'meta[name="miare-api"]' in on_disk


def test_the_static_files_the_viewer_needs_still_serve(base_url: str) -> None:
    """`miare serve` replaces `python -m http.server`, so the page and samples/ stay put."""
    with urllib.request.urlopen(f"{base_url}/score_viewer.html", timeout=10) as response:
        assert response.status == 200
        assert b"opensheetmusicdisplay" in response.read()
    # The sample menu reads the directory index http.server generates.
    with urllib.request.urlopen(f"{base_url}/samples/", timeout=10) as response:
        assert response.status == 200
        assert b"href=" in response.read()


def _worktree_state() -> set[str]:
    return set(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )


def test_the_server_writes_nothing_into_the_repo(base_url: str) -> None:
    """An arrange goes through a temp dir, so the served directory is untouched.

    Compared before and against after rather than asserting a clean tree: this runs in a
    real checkout that may have work in progress, and what is being tested is only that
    the request adds nothing. A stray `support.musicxml` in the root or in samples/ —
    the two places the writer could plausibly land — would show up as a new line.
    """
    before = _worktree_state()
    status, _ = post(
        f"{base_url}/api/arrange?relationship=support&seed=1", FIXTURE.read_bytes()
    )
    assert status == 200
    appeared = _worktree_state() - before
    assert not appeared, f"the server left files behind: {sorted(appeared)}"
