# QA v1 — issue #3, PR #14

Branch `issue-3-hard-constraint-gate`, builder head `1d462e8`, base `staging` at `230843f`.
No UI surface, so no screenshots: this is a library refactor behind a CLI.

## Verdict

Pass. All five acceptance criteria hold, and the one gap the builder declared
open (six of eleven rules untested) is closed in this QA commit.

## AC1 — gate runs before scoring, scorer cannot reject

`check_hard_constraints(profile, slot, state, midi, context, candidate)` at
`src/miare/counterpoint.py:196` returns `Rejection | None`.
`generate_counterpoint_candidates` calls it at `:1002` and skips a rejected
pitch at `:1005`, before `_score_action` at `:1007`. `_score_action` returns
`tuple[CounterpointAction, BeamState]`, no longer optional, and
`tests/test_hard_constraints.py::TestGateIsTotal::test_score_action_has_no_bare_rejection_left`
asserts its source holds no `return None`.

`interval_class` and `spacing` are hoisted onto `CandidateContext`
(`:126-164`); the gate and the scorer read the same values, neither recomputes.

## AC2 — a unit test per rule, no arrangement round trip

Eleven of eleven rules now have direct gate tests. The builder shipped five and
deferred six; QA wrote the six (`unprepared-strong-dissonance`,
`unapproached-weak-dissonance`, `protected-strong-dissonance`,
`leap-over-octave`, `parallel-perfects`, `protected-voice-crossing`). No
follow-up card is needed.

Each rule gets a rejection case and a near-miss case that must pass, so a rule
that stops firing fails a test rather than going quiet.

`mutation-check.txt` — each rule's `return Rejection(...)` was replaced with
`pass` one at a time; every mutant killed at least one test.

## AC3 — line-number comments gone

`tests/test_voice_leading.py:104` and `:112` no longer cite `counterpoint.py`
line numbers; they name rule ids instead.

## AC4 — suite green

`pytest.txt` — `uv run pytest -q` reports 118 passed. Baseline was 73; the
builder took it to 98, QA's six rule groups to 118.

## AC5 — arrangement output byte-identical

`output-identity.sha256` — `tests/fixtures/simple_melody.musicxml` arranged on
`staging` (`230843f`) and on the branch, for `support` and `countermelody`
across seeds 1, 7 and 42. All twelve `.musicxml` files hash to two values, one
per relationship, before matching after. The sidecar `.miare.json` is excluded:
it embeds the absolute output path.

Only the hashes are committed. The arrangements regenerate from the fixture
plus a seed, and twelve copies of the same two files would add 4,600 lines to
the diff. To reproduce, check out `230843f` and the branch side by side and run
`uv run miare arrange tests/fixtures/simple_melody.musicxml --relationship
<support|countermelody> --seed <1|7|42> -o <out>.musicxml` in each.

## Beyond the ACs — differential equivalence probe

`equivalence-probe.py` transcribes `staging`'s eleven inlined rejection paths
verbatim and compares their accept/reject verdict against the new gate over the
cross product of melody anchor, source pitch classes, metric strength, cadence
flag, identity policy, previous pitch, previous melody pitch, previous interval
class, pending resolution, last motion and candidate pitch.

`equivalence-probe.txt` — 1,797,120 combinations, 0 mismatches.

This is the check AC5 approximates. Output identity on one fixture only
exercises the paths that fixture reaches; the probe covers the rule space,
including the two re-orderings worth doubting:

- `protected-voice-crossing` moved out of the scorer's `elif midi >=
  melody_midi` arm into a flat gate condition. `midi >= melody_midi` implies
  `spacing <= 0`, so the scorer's `3 <= spacing <= 19` arm could never also be
  true — the flat form is the same predicate.
- `leap-over-octave` and `parallel-perfects` kept their `previous_midi is not
  None` and `previous_melody is not None` guards.

Determinism holds for a separate reason: `rng.random()` is read once at
`counterpoint.py:876`, after every former rejection point, so a rejected
candidate consumed no randomness before and consumes none now.

## Notes for the reviewer, not defects

- `--seed 1`, `7` and `42` give byte-identical output for a given relationship.
  The same is true on `staging`, so it is not this PR's doing, but a seed that
  changes nothing makes `--seed` and the `rng.random() * 1e-7` lookahead
  tiebreak look decorative. Worth its own card.
- The PR was 438 changed lines before this QA commit, over `auto_max_lines`
  400, so the merge gate routes to a human. QA's tests add to that; the routing
  was already decided.
