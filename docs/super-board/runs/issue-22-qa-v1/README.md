# issue #22 · QA evidence · v1

Every acceptance criterion checked against the real page in a real browser, served by
`python3 -m http.server` from the repo root. Written independently of the builder's
scripts: each check reads an AC off the issue and asserts it on the page.

| File | What it is |
|---|---|
| `qa-checks.mjs` | the 46 checks, one or more per AC, plus the edge cases |
| `checks-chromium.txt` | 46 checks, Chromium, 0 failures |
| `checks-webkit.txt` | 46 checks, WebKit (the engine behind Safari), 0 failures |
| `pytest.txt` | `uv run pytest -q` — 255 passed |
| `ac9-empty-desktop.png` | the page with nothing loaded |
| `ac6-side-by-side-desktop.png` | source and countermelody side by side, phrases marked |
| `ac3-playing-desktop.png` | playing, both playheads on measure 2, transport at 0:02 / 0:53 |
| `ac10-tablet.png` | 1024×768 |
| `ac10-mobile.png` | 375×667, stacked |

## Replaying

Playwright is not a miare dependency and must not become one — AC10 requires no build
step and no new dependency. Install it elsewhere and point it at a served repo.

```console
# in the repo
python3 -m http.server 8422

# in a scratch directory, where node can resolve the import
npm install playwright
npx playwright install chromium webkit
cp /path/to/miare/docs/super-board/runs/issue-22-qa-v1/qa-checks.mjs .
REPO=/path/to/miare PORT=8422 node qa-checks.mjs chromium
REPO=/path/to/miare PORT=8422 node qa-checks.mjs webkit
```

## What was checked beyond the ACs

- **Measure alignment across the whole score.** Seeking to the middle of each of
  Greensleeves' 25 measures, both decks mark that same measure index. The source's
  pickup bar is 0.5 quarters against the arrangement's 3, so the two scores genuinely
  disagree about elapsed time and the shared timeline is what holds them together.
- **Mismatched lengths.** A 25-measure score beside an 8-measure one: a status line
  explains it, the shorter deck's playhead hides past its end, playback pauses at the end.
- **Nothing is fetched to make sound.** The request log over a full playback holds one
  external URL, the OSMD CDN script. No audio, MIDI or soundfont file.
- **Bad input.** Unreadable MusicXML, malformed `.miare.json`, a report naming no open
  score, and a non-score file each give a reason and leave the open score standing.
- **Bounds.** Seeking to the very end, and tempo `0`, `999` and empty, all keep a finite
  duration and a finite playhead position.
- **Samples regenerate.** `./scripts/make-samples.sh` reproduces all four committed
  arrangements byte for byte, and `git check-ignore` confirms the `.gitignore` exception
  really tracks them.

## Tests added

`tests/test_samples.py` gains two cases guarding the contract between the reports and the
page, both seen to fail first:

- `test_report_names_the_two_scores_the_viewer_pairs_it_with` — the viewer attaches a
  report to a deck by the base name in `input` / `output` plus `source.measures`
  (`score_viewer.html:346-359`), and falls back to the last-opened deck when nothing
  matches. A report naming something else therefore draws phrase boundaries over the
  wrong score silently. Before this test, rewriting `input` to an absolute path left the
  suite green.
- `test_report_phrase_boundaries_land_inside_the_source` — a phrase start past the end of
  the source clamps to the last measure rather than erroring, so a wrong offset would
  show up only as boundaries piled on the final bar.
