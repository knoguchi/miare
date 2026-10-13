# QA v1 — issue #6, PR #15

Branch `issue-6-support-anchor-structural-melody` at `be6d287`. Base `staging` at `230ab2d`.
Command: `uv run pytest -q`. Non-visual card — no screenshots; CLI output is the evidence.

## Acceptance criteria

| AC | Result | Proof |
|---|---|---|
| 1. anchor reads structural melody, falls back to `song.melody` | PASS | `src/miare/generation.py:20-26` `_planning_melody` returns `song.structural_melody or song.melody`. Both halves exercised: `test_anchor_returns_structural_pitch_over_passing_tone`, and the fallback by `test_anchor_falls_back_to_surface_melody_when_no_reduction` (added in QA — see Test gaps). |
| 2. both anchors share `(song, start, end)` | PASS | `inspect.signature` of `generation._melody_anchor` and `counterpoint._melody_anchor` both `(song, start, end) -> int \| None`. |
| 3. new fixture prunes, with a test asserting the inequality | PASS | `tests/fixtures/passing_tones.musicxml` measured melody **23** / structural **21**. `test_fixture_actually_prunes`. |
| 4. slot over a pruned passing tone anchors structurally | PASS | Measured red-first against the pre-fix anchor: the old `(start, duration)` anchor returned the passing tone itself for both pruned notes — `P1:m1:e3` midi 65 -> 65, `P1:m4:e21` midi 64 -> 64. The new anchor returns the governing structural pitch. `test_generated_slot_over_passing_tone_anchors_structurally` reads `melody_midi` off the real `_support_spans`. |
| 5. `uv run pytest -q` passes, at least 73 tests | PASS | **124 passed**. `staging` is 118, so the branch adds 6 (5 from the builder, 1 from QA). `pytest.log`. |
| 6. support note count before/after, after lower or equal | PASS | `passing_tones.musicxml`, `--relationship support --seed 1`: **before 32, after 32**. Equal, so "lower or equal" holds. The output does differ — pitches moved in 2 places — so the anchor change took effect without changing density. `support-before-staging.musicxml` / `support-after-branch.musicxml`. |
| 7. `countermelody` byte-identical to `staging` on both existing fixtures | PASS | `simple_melody` and `minor_melody` byte-identical on `countermelody`, and on `support` as well (neither fixture prunes). `cmp` on the `.musicxml` only, per the AC. |

## Red-first verification

The builder's tests were re-run against the pre-fix source to confirm they are not
tautological. With `staging`'s `_melody_anchor`, both pruned notes anchor to the passing
tone — the exact defect the card describes. The two assertions that matter fail there and
pass here.

The QA-added fallback test was mutation-checked the same way: with `_planning_melody`
narrowed to `return song.structural_melody`, it fails; restored, it passes.

## Test gaps

**Written in QA (High).** AC1 has two halves and the builder tested one. No test covered
`song.structural_melody` being empty, so the `or song.melody` fallback was unverified
behaviour on an explicit AC. `test_anchor_falls_back_to_surface_melody_when_no_reduction`
covers it: every surface note anchors to its own pitch when the reduction is empty, and the
two anchors disagree over a pruned note, which keeps the test from passing vacuously.
Tests only — no app code changed.

**Not written (Low).** `boundary_melody_midi` (`generation.py:119-125`) still reads
`song.melody` directly. It is not in scope: it is the note *sounding* just before the slot,
used for voice-leading continuity, where the surface pitch is the correct reading. Flagged
for the reviewer as a deliberate non-change, not an oversight.

## Note on the card's premise

The card's stated consequence — over-dense accompaniment — does not reproduce. The note
count is unchanged on every score available here (32->32 new fixture, 16->16 both old
fixtures, and the builder measured 92->92 on `Rencon_Final`). What does land is the §7.7
layering rule and the removal of the `(start, duration)` / `(start, end)` signature trap.
AC6 was written as "lower or equal" and equal satisfies it, so this is a pass, not a
failure — but the density claim in the ticket is not evidence for the change, and the
reviewer should weigh it on the layering and signature grounds the builder stated.
