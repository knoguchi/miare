# QA v1 — issue #7 · PR #17 @ 24588de

Base: `origin/staging` @ 9311258 · branch `issue-7-shared-theory-helpers`

## AC1 — both private duplicates deleted, call sites moved

`grep -rn "_measure_at\|_measure_for_time" src/` → no hits in `src/`.
`measure_for_time` call sites: `analysis.py:120,322,485,596` (4) and
`counterpoint.py:309` (1), plus one import each. On `staging` the two `def`s
plus their calls counted 5 and 2, so every call site moved.
`inspect.getsource` on both `staging` originals vs `theory.measure_for_time`:
bodies identical character for character, so the extraction is a true no-op.

## AC2 — `scale_intervals` returns intervals only

`theory.scale_intervals` returns the bare tuple; `analysis._scale` is a one-line
delegate (`analysis.py:215-216`) and its two internal callers are untouched;
`counterpoint._tonal_pitch_classes` (`counterpoint.py:508-512`) adds
`song.tonic_pc` and takes mod 12 at the call site.

## AC3 — suite green

`uv run pytest -q` → **146 passed** (see `pytest.log`).
`staging` collects 124; the branch adds 17 builder tests + 5 QA guard tests.
No test was lost or skipped.

## AC4 — arrangement output byte-identical

Each pair run on `staging` and on the branch, same seed, compared with `cmp`.
md5 manifest: `output-md5.txt` — every before/after pair matches.

| Fixture | Relationship | Seed | md5 (before = after) |
|---|---|---|---|
| `tests/fixtures/simple_melody.musicxml` | support | 1 | `967a405a7371acc6b7184affa84f150e` |
| `tests/fixtures/simple_melody.musicxml` | countermelody | 1 | `37ad74a8afc568ed52ead757c144b9bd` |
| `tests/fixtures/minor_melody.musicxml` | support | 1 | `155ab4bb3437ada00a9e4738e3392a9c` |
| `tests/fixtures/minor_melody.musicxml` | countermelody | 1 | `7975b7f7cf59563637831a8f67c5913c` |
| `rencon_miare.musicxml` | support | 7 | `4c40c3aa60dfdde5ffc1504f4d0851e0` |
| `rencon_miare.musicxml` | countermelody | 7 | `bf853543cb165958ad101f549425a1e1` |

The AC named one fixture in major mode only. `scale_intervals` branches on
`mode.startswith("min")`, so the major-only run never reaches the minor tuple;
the minor fixture and a full real score were added to cover both branches.

## Test gaps written (red-first)

`TestNoModuleKeepsItsOwnCopy` in `tests/test_shared_theory.py`. The builder's
`test_duplicated_private_helpers_are_gone` pins the two old *names*, so a copy
under a third name would pass it. The new tests pin the *rule*: neither
`analysis` nor `counterpoint` may contain the lookup body or either scale
literal, plus one guard-the-guard test asserting the probes still match
`theory.py` (so renaming the helper cannot silently disarm them).

Proven red: reinserting `_measure_at` and inlining the minor tuple into
`counterpoint.py` failed 4 tests, including both new guards. Restored, green.

Written as unit tests, not e2e: the defect is a source-duplication rule, and no
browser or UI is involved.

## Test gaps not written

- **Low** — `measure_for_time` raises `IndexError` on a score with no measures.
  Both `staging` originals did the same; it is pre-existing behaviour, out of
  scope for a no-op refactor, and no caller can reach it (parsing rejects a
  score with zero measures upstream).

No screenshots: no UI in this change.
