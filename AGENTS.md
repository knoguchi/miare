# miare

Analyses a finished piece and re-arranges it for a different ensemble, using that piece's own
musical vocabulary and grammar as the reference. MusicXML in, two-voice score out.

## Commands

| Task | Command |
|---|---|
| Install deps | `uv sync` |
| Run tests | `uv run pytest -q` |
| Run a single test | `uv run pytest tests/test_<name>.py -q` |
| CLI (in repo) | `PYTHONPATH=src python3 -m miare <analyze\|arrange\|inspect\|serve> …` |
| CLI (installed) | `miare --help` |
| Viewer + arrange API | `make viewer` (`miare serve --port 8765`) |

`pyproject.toml` sets `pythonpath = ["src"]` and `testpaths = ["tests"]`, so pytest needs no
`PYTHONPATH`. Running the module directly does.

## Where things live

| Path | Responsibility |
|---|---|
| `src/miare/cli.py` | argparse entry point; `analyze`, `arrange`, `inspect`, `serve` subcommands |
| `src/miare/pipeline.py` | the analyse/arrange sequence itself, independent of where the score came from — the CLI and the server both call it, so neither becomes a second implementation |
| `src/miare/server.py` | `miare serve` — localhost-only stdlib HTTP server: the repo statically, plus `POST /api/analyze`, `POST /api/arrange`, `GET /api/health` |
| `src/miare/musicxml.py` | MusicXML read/write |
| `src/miare/ir.py` | internal representation — the data model everything else passes around |
| `src/miare/analysis.py` | melody, phrase, key, harmony candidates, `MusicalLanguage`, structural melody |
| `src/miare/generation.py` | builds the supporting voice |
| `src/miare/counterpoint.py` | builds the countermelody voice (beam search) |
| `src/miare/instruments.py` | instrument profiles (range, idiom) |
| `src/miare/theory.py` | music-theory facts shared by analysis and generation — measure lookup, scale intervals |
| `src/miare/constraints.py` | voice-leading rules both generators read — hard constraints (leap limit, parallel perfects) and preference weights (contrary motion, voice crossing) |
| `score_viewer.html` | single-file browser viewer: renders one arrangement, plays it back as MIDI synthesized from the MusicXML, and can mute down to a single part. Under `miare serve` a dropped score can also be arranged in place; under a static server that control is hidden. No build step, CDN scripts only |
| `samples/<name>/` | the viewer's demo set — a committed `source.musicxml` plus the arrangements and `.miare.json` reports `scripts/make-samples.sh` writes beside it |
| `scripts/make-samples.sh` | regenerates `samples/` from its committed sources; run it after an arranger change |
| `tests/` | pytest suite |
| `DESIGN.md` | internal responsibilities, data model, processing order (Japanese) |
| `PLAN.md` | product intent (Japanese) |
| `docs/super-board/PROJECT.md` | orientation for autonomous lane agents |

## Conventions

- Docs are bilingual: EN and JA both fine. Existing `README.md`, `DESIGN.md`, `PLAN.md` are
  Japanese — match the surrounding language when editing one of them, and write new docs in
  either. Code, identifiers, and commit messages are English.
- Read `DESIGN.md` before changing analysis or generation behaviour — it defines the
  responsibilities and processing order those modules are meant to follow.
- Arrangement output is deterministic given a source score plus `--seed`. DON'T introduce
  unseeded randomness.
- Generated artefacts (`*.miare.json`, `*.mscz`, root `*.musicxml`) are gitignored outputs.
  Test fixtures under `tests/fixtures/` and the viewer's demo set under `samples/` are the
  exceptions and ARE committed. A sample that no longer regenerates fails
  `tests/test_samples.py` — re-run `./scripts/make-samples.sh` and commit the result.

## Boundaries

| | |
|---|---|
| Always | run `uv run pytest -q` before handing work off; add a test with every behaviour change |
| Ask first | changing the IR shape in `ir.py`, or the `.miare.json` on-disk format |
| Never | commit generated scores to the repo root; add unseeded randomness to arrangement |

<!-- super-board:begin v3.1.2 (managed; edits inside are overwritten on upgrade) -->
## Super Board

Board: Backlog · Ready · Building · QA · Review · Blocked · Done. Labels route: `qa` skips Building · `bug`, `feature`, none → built first.

| When | Use | NEVER |
|---|---|---|
| Set up / repair the board | `/super-board onboard` | DON'T hand-edit config mid-run |
| Check tickets before a run | `/super-board lint` | NEVER run with AC-less tickets |
| Drain the board | `/super-board run` | NEVER build, test or merge from the orchestrator |
| Board state / halt | `/super-board status` · `/super-board stop` | |
| Build one ticket | `/super-build` | NEVER build outside the card's worktree |
| QA a branch, or a live URL alone | `/super-qa` · `/super-qa <url>` | NEVER mark QA pass without evidence |
| Review a PR, merge | `/super-review` | NEVER `gh pr merge` direct — merge gate only |
| File bugs from Sentry, PostHog, PRs | `/super-collect` | DON'T file without a verifier pass |
| UI polish | `/ui-refine-loop` (human runs it) | board NEVER runs it |
| Diagram / explainer page | `/visual` | |
| Commit, pull, push this branch | `/git-sync` | NEVER force push or rebase shared history |

| Rule | Value |
|---|---|
| Config | `.claude/super-board/configs/<slug>.json` |
| Isolation | 1 card · 1 worktree · 1 branch |
| Merge | auto for normal changes · money, auth, destructive schema (DROP/TRUNCATE/RENAME) → human · PR > 400 changed lines → human |
| Migrations | additive → robot migrates allowed DBs only · live DB ALWAYS → 🙋 needs you |
| Blocked card | block-template comment · last line `blocked-by:` · 🙋 = needs you |
| Secrets | NEVER read `.env`; key names via `.claude/bin/super-board-env-check.sh` |

## Writing (super-board)

| Thing | Format → `.claude/skills/super-board/references/writing-standard.md` |
|---|---|
| Commit, PR title | `<emoji> [type] scope: subject` + short bullets · ✨ feat 🐛 fix 🔧 chore ♻️ refactor 🧪 test 📝 docs |
| PR body | blocks: status · Problem · Solution · AC + proof · history · Before\|After · Risk — `super-board-pr-body.sh` |
| Ticket | Problem · Context · Fix · AC · Risk · Blocked by → `docs/agents/issue-tracker.md` |
| Comment | `[role] [label] status` · Did · ✅ Done · ❌ Not done · Next · ≤ 8 lines |
| PR author notes | file + critical inline review comments: Purpose · What changed · Why it matters → `pr-author-notes.md` |

- NEVER chain steps with arrows. One step per line, lettered under Where.
- NEVER link screenshots. Embed a raw URL pinned to a sha.
- DON'T repeat file lists in status comments. DON'T write "Not verified" or "Next" in a PR body.
<!-- super-board:end -->

## Commits and PRs

Conventional Commits. Subject ≤ 50 chars, imperative. Body only when the "why" isn't obvious
from the diff.
