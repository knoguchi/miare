"""A localhost server that puts the real analyser and arranger behind the viewer.

`score_viewer.html` can render a dropped score on its own, but until now it had no way to
*arrange* one — that needed a terminal. This serves the repo directory exactly as
`python -m http.server` did, so the sample menu and the committed `samples/` keep working,
and adds three endpoints the page probes for and calls.

Standard library only, deliberately: the project ships with `dependencies = []`, and
putting a web framework in the install just to answer two POSTs would be the largest
dependency in the tree.

This runs arbitrary user files through a parser, so it is not a service:

- it binds 127.0.0.1, never 0.0.0.0, so nothing on the network can reach it;
- a request carrying a cross-origin `Origin` is refused, so a page the user happens to
  have open in another tab cannot drive it;
- the request body is capped, because the parser holds the whole score in memory;
- nothing is written into the served directory. The MusicXML writer needs a file to write
  to and `validate_round_trip` needs to read it back, so each arrange gets a fresh temp
  directory that is removed before the response is sent.
"""

from __future__ import annotations

import json
import tempfile
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import __version__
from .instruments import PROFILES
from .ir import to_jsonable
from .pipeline import analyze_source, arrange_source


HOST = "127.0.0.1"
MAX_BODY_BYTES = 10 * 1024 * 1024        # a MusicXML score is kilobytes; 10 MB is slack


POSTED_NAME = "posted.musicxml"


def _scrub(message: str, tmpdir: str) -> str:
    """Keep the server's temp paths out of a response.

    The pipeline names the file it was given in its errors, which is what a CLI user
    wants. Over HTTP that path is a scratch directory the caller never asked about, so
    it is replaced with the neutral name the body was stored under.
    """
    return message.replace(str(Path(tmpdir).resolve()) + "/", "").replace(tmpdir + "/", "")


class ApiError(Exception):
    """An error with an HTTP status, rendered as {"error": ...} rather than a traceback."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    raw = query.get(name, [None])[0]
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"{name} must be an integer, got {raw!r}"
        ) from None


def _query_flag(query: dict[str, list[str]], name: str) -> bool:
    """Present-and-not-false is true, so `?allow_lossy_input=1` and a bare `?x` both work."""
    if name not in query:
        return False
    raw = (query[name][0] or "").strip().lower()
    return raw not in {"0", "false", "no"}


def _query_profile(query: dict[str, list[str]], name: str, default: str) -> str:
    value = query.get(name, [None])[0] or default
    if value not in PROFILES:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"{name} {value!r} is not an instrument profile; "
            f"choose one of {', '.join(sorted(PROFILES))}",
        )
    return value


def run_analyze(body: bytes) -> dict[str, Any]:
    """POST /api/analyze — the body is MusicXML, the answer is what `miare analyze` writes."""
    with tempfile.TemporaryDirectory(prefix="miare-serve-") as tmpdir:
        source = Path(tmpdir) / POSTED_NAME
        source.write_bytes(body)
        try:
            return analyze_source(source)
        except ValueError as error:
            raise ValueError(_scrub(str(error), tmpdir)) from error


def run_arrange(body: bytes, query: dict[str, list[str]]) -> dict[str, Any]:
    """POST /api/arrange — MusicXML in, the arrangement and its full report out.

    The arrangement comes back as a string rather than a URL because nothing is kept: the
    temp directory it was written to is gone by the time this returns.
    """
    relationship = query.get("relationship", ["countermelody"])[0] or "countermelody"
    options = {
        "relationship": relationship,
        "voice1_profile": _query_profile(query, "voice1_profile", "violin"),
        "voice2_profile": _query_profile(query, "voice2_profile", "cello"),
        "seed": _query_int(query, "seed", 1),
        "beam_width": _query_int(query, "beam_width", 32),
        "context_measures": _query_int(query, "context_measures", 2),
        # The browser has no CLI flag, so without this a score the CLI can arrange would
        # dead-end in the viewer with no way forward.
        "allow_lossy_input": _query_flag(query, "allow_lossy_input"),
    }
    name = (query.get("name", [""])[0] or "dropped.musicxml").split("/")[-1]
    stem = name.rsplit(".", 1)[0] or "arrangement"
    with tempfile.TemporaryDirectory(prefix="miare-serve-") as tmpdir:
        source = Path(tmpdir) / POSTED_NAME
        source.write_bytes(body)
        output = Path(tmpdir) / f"{relationship}.musicxml"
        try:
            report = arrange_source(
                source,
                output,
                input_label=name,
                # Named after the score it came from, because the viewer titles its
                # diagnostics with this and "countermelody.musicxml" says nothing about
                # which score is on screen.
                output_label=f"{stem}.{relationship}.musicxml",
                **options,
            )
        except ValueError as error:
            raise ValueError(_scrub(str(error), tmpdir)) from error
        musicxml = output.read_text(encoding="utf-8")
    return {"musicxml": musicxml, "report": report}


class MiareHandler(SimpleHTTPRequestHandler):
    """Static files as `http.server` already serves them, plus the API."""

    server_version = f"miare/{__version__}"

    def do_POST(self) -> None:                       # noqa: N802 — BaseHTTPRequestHandler's name
        route = urlparse(self.path)
        try:
            self._check_origin()
            if route.path == "/api/analyze":
                payload = run_analyze(self._read_body())
            elif route.path == "/api/arrange":
                payload = run_arrange(
                    self._read_body(), parse_qs(route.query, keep_blank_values=True)
                )
            else:
                raise ApiError(
                    HTTPStatus.NOT_FOUND, f"no POST endpoint at {route.path}"
                )
        except ApiError as error:
            self._send_json({"error": error.message}, error.status)
        except ValueError as error:
            # Everything the pipeline rejects — unsupported notation, an unimplemented
            # relationship, a failed round trip, a score it cannot parse — arrives as a
            # ValueError, and all of them are the caller's input being wrong.
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:                   # noqa: BLE001 — never leak a traceback
            self._send_json(
                {"error": f"{type(error).__name__}: {error}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        else:
            self._send_json(payload, HTTPStatus.OK)

    # The viewer must not probe for the API with a request that 404s under a static
    # server: a browser logs every failed fetch to the console, and the page is supposed
    # to be silent there. So the server announces itself in the HTML it serves instead,
    # and the page reads the tag. /api/health stays for curl and the tests.
    API_MARKER = b'<meta name="miare-api" content="1">'
    HEAD_TAG = b"<head>"

    def do_GET(self) -> None:                        # noqa: N802
        if urlparse(self.path).path == "/score_viewer.html":
            if self._send_viewer_with_marker():
                return
        if urlparse(self.path).path == "/api/health":
            # What the viewer probes to decide whether to offer the Arrange control. It
            # answers on GET so the page can ask without a preflight.
            self._send_json(
                {
                    "ok": True,
                    "miare_version": __version__,
                    "relationships": ["support", "countermelody"],
                    "profiles": sorted(PROFILES),
                },
                HTTPStatus.OK,
            )
            return
        super().do_GET()

    def _send_viewer_with_marker(self) -> bool:
        """Serve score_viewer.html with the API marker added to its <head>.

        Returns False if the file is not there to serve, so the normal static handler can
        answer (and produce its own 404). The page on disk is never modified — the tag is
        added on the way out, which is also why it cannot go stale.
        """
        viewer = Path(self.translate_path(self.path))
        if not viewer.is_file():
            return False
        try:
            html = viewer.read_bytes()
        except OSError:
            return False
        # Keyed on the <head> tag, not on the marker's absence: the page's own source
        # mentions the marker in a comment, so an "already present?" check would see it
        # there and never inject the real tag.
        html = html.replace(self.HEAD_TAG, self.HEAD_TAG + b"\n" + self.API_MARKER, 1)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)
        return True

    def _check_origin(self) -> None:
        """Refuse a cross-origin caller, so only a page served from here can post."""
        origin = self.headers.get("Origin")
        if origin is None:
            return                                   # curl, and same-origin fetch in some browsers
        host = urlparse(origin).hostname
        if host not in {"localhost", "127.0.0.1", "::1", "[::1]"}:
            raise ApiError(
                HTTPStatus.FORBIDDEN, f"origin {origin} is not localhost"
            )

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, "Content-Length is not a number"
            ) from None
        if length <= 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "empty request body")
        if length > MAX_BODY_BYTES:
            # Answering and closing mid-upload gives the client a broken pipe instead of
            # the 413, so read the declared body off the socket first — in bounded
            # chunks, and never more than the client said it would send. The parser
            # still never sees it.
            self._discard(length)
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"score is {length} bytes; the limit is {MAX_BODY_BYTES}",
            )
        body = self.rfile.read(length)
        if len(body) != length:
            raise ApiError(HTTPStatus.BAD_REQUEST, "request body ended early")
        return body

    def _discard(self, length: int) -> None:
        """Read and drop `length` bytes, so the response reaches the client."""
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                return                               # client gave up; nothing to drain
            remaining -= len(chunk)

    def _send_json(self, payload: Any, status: HTTPStatus) -> None:
        body = (
            json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(root: Path, port: int = 0) -> ThreadingHTTPServer:
    """Bound and ready, not yet serving — port 0 picks a free one, which tests want."""
    directory = str(Path(root).resolve())

    class BoundHandler(MiareHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=directory, **kwargs)

    return ThreadingHTTPServer((HOST, port), BoundHandler)


def serve(root: Path, port: int) -> int:
    httpd = make_server(root, port)
    actual = httpd.server_address[1]
    print(f"→ http://{HOST}:{actual}/score_viewer.html   (ctrl-c to stop)")
    print("  API: POST /api/analyze · POST /api/arrange · GET /api/health")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
    return 0
