# QA v1 — issue #8 · PR #20 @ `4bcfff2`

Branch `issue-8-shared-voice-leading-constraints`, base `staging` @ `3513394`.
Non-visual change (library + CLI), so no screenshots.

## Acceptance criteria

| # | AC | Verdict | Proof |
|---|---|---|---|
| 1 | Four rules defined once in `constraints.py`, read by both generators | ✅ | `src/miare/constraints.py:39-110`; `generation.py:6-13` and `counterpoint.py:9-17` import them |
| 2 | `PERFECT_INTERVAL_CLASSES` is the only definition, no inline `{0, 7}` | ✅ | `grep -rn "{0, 7}" src/` → `constraints.py:39` only |
| 3 | `leap > 12` is a hard rejection in both generators | ✅ | `generation.py:207-210` returns `float("-inf")`; `counterpoint.py:298`; `tests/test_constraints.py:195` |
| 4 | Contrary motion `+1.1`, voice crossing `-3.0` in both | ✅ | `constraints.py:44-45`; both generators add the constants |
| 5 | Stale comments and docstring in `tests/test_voice_leading.py` corrected | ✅ | diff of `:1-21`, `:196-199`, `:207-220` |
| 6 | `uv run pytest -q` passes with ≥ 73 tests | ✅ | 241 passed — `pytest.log` |
| 7 | Before/after note counts pasted; `countermelody` byte-identical | ✅ with a correction | table below |

## AC7 — before vs after

3 fixtures × 2 relationships × 6 seeds (1, 2, 3, 7, 11, 42) = 36 arrangements,
`staging` vs branch, compared with `cmp`.

| Fixture | Relationship | Notes before | Notes after | Bytes |
|---|---|---|---|---|
| simple_melody | support | 28 | 28 | identical |
| simple_melody | countermelody | 38 | 38 | identical |
| minor_melody | support | 27 | 27 | identical |
| minor_melody | countermelody | 38 | 38 | identical |
| passing_tones | support | 55 | 55 | identical |
| passing_tones | countermelody | 46 | 46 | identical |

All 36 pairs are byte-identical. The ticket predicted the `support` path would
change; it does not. The builder reported this and the reason holds up:

- The rules are reached in quantity — instrumenting a real `arrange_support`
  run counts 1660 leap checks (760 over an octave), 900 contrary-motion
  checks (290 hits), 184 crossing checks (45 hits) and 26 parallel-perfect
  hits on `simple_melody` alone. So this is not dead code.
- But the *winning* DP path never touches a tightened rule. The chosen support
  line's widest leap is 5 semitones on every fixture (`leap <= 5` pays +1.4 and
  `-leap * 0.28` punishes width), so the leap limit never binds the argmax —
  `-4.0` and `-inf` pick the same path. The chosen path has zero crossings on
  all 3 fixtures × 3 seeds, so `-5.0 → -3.0` never binds either. `+1.2 → +1.1`
  shifts every contrary transition by the same 0.1 and does not flip a ranking
  on these inputs.

Note the direction: the ticket called all three coefficients a tightening, but
voice crossing `-5.0 → -3.0` **loosens** the support generator. Harmless here
(no chosen path crosses), but it is a loosening, not a tightening.

## Mutation testing — do the new tests bite?

Each mutation applied to the branch, full suite run, then reverted.

| Mutation | Tests failing | Caught |
|---|---|---|
| `generation.py` leap ban → `value -= 4.0` (the original bug) | 7 | ✅ |
| `generation.py` drops the parallel-perfect ban | 6 | ✅ |
| `MAX_LEAP_SEMITONES` 12 → 13 | 5 | ✅ |
| `CONTRARY_MOTION_BONUS` 1.1 → 1.2 | 1 | ✅ |
| `VOICE_CROSSING_PENALTY` -3.0 → -5.0 | 1 | ✅ |
| `generation.py` re-inlines `value += 1.1` | 0 | ❌ |
| `counterpoint.py` re-inlines `score.add(-3.0, ...)` | 0 | ❌ |

The two survivors are the same gap, recorded below. Both are duplication the
anti-drift guard is supposed to catch; neither changes behaviour, because the
re-inlined number equals the shared constant.

## Test gaps (not written)

- **Medium — the anti-drift guard is one-sided.**
  `tests/test_constraints.py:236-247` probes for the *old divergent* literals
  (`{0, 7}`, `1.2, "contrary motion"`, `-5.0`, `value -= 4.0`). A future copy
  that inlines the *resolved* value — `1.1` in `generation.py`, `-3.0` in
  `counterpoint.py` — is a silent re-divergence waiting to happen and no test
  fails. Fixing it needs a probe on the resolved values too, which is a change
  to the guard's design rather than one more assertion, so it is left for the
  reviewer to rule on rather than patched here.
- **Low — no fixture makes a tightened coefficient flip the output.** Every
  input in the repo is byte-identical before and after, so the three
  coefficient resolutions have no behavioural regression test. A synthetic
  fixture whose argmax a 0.1 contrary-motion delta flips would pin them.

## Commands

```
uv run pytest -q                                   # 241 passed
uv run miare arrange tests/fixtures/<f>.musicxml --relationship <r> --seed <s> -o <out>
```
