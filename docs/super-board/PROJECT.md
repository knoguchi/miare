# miare — Project Orientation

`miare` analyzes a completed MusicXML score and re-arranges it for a new
ensemble, deriving harmony and new voices from *that score's own* musical
vocabulary. The MVP produces a deterministic two-voice arrangement.

## Commands

Python >= 3.11. No runtime dependencies; `pytest` is the only dev dependency.

```console
uv sync                       # install (uses uv.lock; dev group has pytest)
uv run pytest -q              # 73 tests, ~0.5s — no network, no MuseScore needed
PYTHONPATH=src pytest -q      # equivalent if deps already installed
```

`pyproject.toml` sets `pythonpath = ["src"]` and `testpaths = ["tests"]`, so
plain `pytest` from the repo root works inside an activated venv.

CLI (console script `miare = miare.cli:main`; `python3 -m miare` also works):

```console
uv run miare analyze input.musicxml -o analysis.json
uv run miare arrange input.musicxml -o out.musicxml \
  --voice1-profile violin --voice2-profile cello \
  --relationship support --seed 1
uv run miare arrange input.musicxml -o counter.musicxml \
  --relationship countermelody --beam-width 32 --context-measures 2 --seed 1
uv run miare inspect input.musicxml [--json]
```

Other `arrange` flags: `--allow-lossy-input`, `--report PATH`.
Editable install alternative: `python3 -m pip install -e .`.

## Where things live

All source is in `src/miare/` (flat package, no sub-packages).

| Module | Responsibility |
| --- | --- |
| `ir.py` | All dataclasses: `Pitch`, `Event`, `Measure`, `Part`, `SourceScore`, `Phrase`, `HarmonyCandidate`, `HarmonicSlice`, `MusicalLanguage`, `SongIR`, `GeneratedEvent`, `InstrumentProfile`, `OutputVoice`, `ArrangementIR`, plus `to_jsonable` / `fraction_text`. No I/O, no music logic. |
| `musicxml.py` | Only module that touches XML. `parse_musicxml` → `SourceScore`; `write_arrangement` → MusicXML; `validate_round_trip` re-parses output and compares; `_duration_notation` maps `Fraction` durations to note type/dots/time-modification. |
| `analysis.py` | `SourceScore` → `SongIR`. Key inference (Krumhansl profiles), `select_melody`, `detect_phrases`, `infer_harmony` (chord vocabulary + transition scoring), `refine_phrases_with_harmony`, `extract_musical_language`, `reduce_to_structural_melody`. Entry point: `analyze_score`. |
| `generation.py` | `relationship="support"`: `generate_support_voice` scores candidate pitches per harmonic slice; `validate_arrangement` enforces hard constraints (range, positive duration, monophony); entry point `arrange_support`. |
| `counterpoint.py` | `relationship="countermelody"`: beam search over `CounterpointSlot`s. `build_counterpoint_plan` → `CounterpointPlan` (spans with `identity_policy`, texture, motif signature); `_score_action` holds the critic weights and the hard rules; `generate_counterpoint_candidates` runs the beam; entry point `arrange_countermelody`. Largest module (~1050 lines). |
| `instruments.py` | `PROFILES` dict (violin, viola, cello, flute, bassoon) + `get_profile`. Ranges, preferred ranges, clef. `generated_from_source` maps source notes into an instrument's register (phrase-coherent octave displacement, Viterbi). |
| `theory.py` | Music-theory facts both `analysis.py` and `counterpoint.py` need, one copy each: `measure_for_time` (the measure whose half-open span holds a time) and `scale_intervals` (semitone offsets above the tonic). Pure; imports only `ir`. Voice-leading rules are not here — those belong in `constraints.py`. |
| `cli.py` | argparse front end: `analyze`, `arrange`, `inspect`. Also owns MuseScore `.mscz`/`.mscx` conversion shims and the JSON report writer. |
| `__main__.py` | `python3 -m miare` shim. |

Tests: `tests/test_theory.py` (key/chord/cadence/phrase/structural-melody units),
`tests/test_voice_leading.py` (counterpoint and support voice-leading rules),
`tests/test_pipeline.py` (end-to-end parse → analyze → arrange → round-trip, CLI,
determinism). Fixtures: `tests/fixtures/simple_melody.musicxml`, `minor_melody.musicxml`.

## Domain concepts & formats

- **`.musicxml`** — the only format parsed/written natively. `score-partwise`
  only, single movement.
- **`.mscz` / `.mscx`** — MuseScore files. Handled *only* by shelling out to the
  MuseScore CLI (`mscore`/`mscore4`/`musescore`, or the macOS app bundle) via
  `_as_musicxml_input` / `_as_musicxml_output` in `cli.py`. Fails loudly if
  MuseScore is not installed.
- **`.miare.json`** — the provenance/report sidecar `arrange` always writes
  (default `OUTPUT.miare.json`). Contains `miare_version`, input/output paths,
  `seed`, `source` inspection, full `analysis` (`SongIR`), full `arrangement`
  (`ArrangementIR` incl. per-note `provenance`), and `round_trip`. It is a
  dump via `to_jsonable`, not a hand-written schema.

Pipeline: `parse_musicxml` → `SourceScore` → `analyze_score` → `SongIR`
(melody, phrases, harmonic slices with ranked candidates, `MusicalLanguage`,
structural melody) → `arrange_support` or `arrange_countermelody` →
`ArrangementIR` → `validate_arrangement` (hard constraints) →
`write_arrangement` → `validate_round_trip` (re-parse and compare parts,
measure lengths, pitches, onsets, durations, ties) → report JSON.

## Conventions

- `from __future__ import annotations` at the top of every module; modern
  typing (`X | None`, `list[str]`) everywhere; functions are annotated.
- All musical time is `fractions.Fraction` — never float. Pitch is MIDI int
  plus spelled `Pitch(step, octave, alter)`.
- Plain `@dataclass` (frozen where immutable) for data; no Pydantic, no ORM.
- Module-private helpers are `_leading_underscore`; public entry points are
  `analyze_score`, `arrange_support`, `arrange_countermelody`,
  `parse_musicxml`, `write_arrangement`, `validate_*`.
- Validators return `list[str]` of error messages (empty = OK) rather than
  raising; `cli.py` turns them into `ValueError` → `parser.error`.
- Tests: pytest, no fixtures/conftest, grouped in `class TestX:` with a
  docstring stating the musical rule under test; module docstrings explain
  which rules are hard (structurally impossible) vs soft (seed-dependent).
  Helpers at module scope prefixed `_`.
- Japanese in `README.md` / `DESIGN.md` / `PLAN.md`; English in code and
  comments.

## Gotchas

- **Determinism is a contract.** Same seed + same input must give identical
  output (`test_same_seed_is_reproducible`). Only use the seeded `Random`
  already threaded through; never `random` module-level, dict iteration order
  over unsorted sets, or time/UUID.
- **`validate_round_trip` will reject your output.** Any change to
  `write_arrangement` must keep parts, measure lengths, pitches, onsets,
  durations and ties byte-equivalent on re-parse, or `arrange` deletes the
  file and errors.
- **Unimplemented relationships must error, never silently degrade.**
  `dialogue`, `imitation`, `equal`, `auto` are accepted by argparse (they're in
  the IR/design) but `_cmd_arrange` raises for them. Keep it that way.
- **`UNSUPPORTED-OUTPUT:` diagnostics are a hard gate.** Lyrics, repeat
  barlines, chord symbols, key changes etc. abort `arrange` unless
  `--allow-lossy-input`. Adding new lossy notation means adding a new
  `UNSUPPORTED-OUTPUT:` diagnostic in `musicxml.py`, not silently dropping it.
- **Layering rule from DESIGN.md §17:** `analysis` must not import XML
  elements; `generation` must not depend on external models; notation-driven
  fixes belong in the instrument/output stage, not in `SongIR`.
- **`.miare.json`, `.mscz`, and root-level `.musicxml` are gitignored**
  (`tests/fixtures/*.musicxml` is explicitly re-included). Don't try to commit
  generated scores; don't add new test fixtures outside `tests/fixtures/`.
- The many score files at the repo root (`bach.*`, `swan_v*.*`, `rencon_*`,
  `tempest-*`, `pathetique-*`) are untracked scratch outputs, not fixtures.
- `.mscz` paths require MuseScore installed; tests never exercise them, so a
  change there is unverified by CI — test with `.musicxml` first.
- Suite is fast (~0.5s). Always run the full suite; there is no reason to
  select a subset.
