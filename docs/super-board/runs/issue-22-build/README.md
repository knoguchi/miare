# issue #22 · builder evidence

Browser checks for `score_viewer.html`, run against `python3 -m http.server` in the repo root.

| File | What it is |
|---|---|
| `browser-checks.txt` | 64 checks, Chromium and WebKit, 0 failures |
| `viewer-checks-1.mjs` | render, playback, playhead, audio switch, drop, mobile |
| `viewer-checks-2.mjs` | sample menu, click-to-seek, dropped score + report pair, bad input |
| `01-empty-desktop.png` | the page with nothing loaded |
| `02-greensleeves-desktop.png` | source and countermelody side by side, phrases marked |
| `03-greensleeves-playing.png` | playing, both playheads on the same measure |
| `04-short-tune-tablet.png` | 1024×768 |
| `05-short-tune-mobile.png` | 375×667, stacked |

Replaying them needs Playwright, which miare does not depend on and must not: the page
has no build step. Install it somewhere else and run the scripts from there.

```console
# in the repo
python3 -m http.server 8411 &

# in a scratch directory, where node can resolve the import
npm install playwright@1.48
npx playwright install chromium webkit
cp /path/to/miare/docs/super-board/runs/issue-22-build/viewer-checks-*.mjs .
REPO=/path/to/miare PORT=8411 node viewer-checks-1.mjs chromium
```

`uv run pytest -q` covers what lives in the repo: `tests/test_samples.py` checks the
committed samples still regenerate from their sources and still hold what the page reads.
