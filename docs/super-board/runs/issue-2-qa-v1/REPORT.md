# QA evidence · issue #2 · v1

Branch `issue-2-harmony-candidate-pcs` · builder head `b256608` · base `bbae334` (staging).
Non-visual card: no screenshots. All evidence is command output.

## Suite

| Tree | Command | Result |
|---|---|---|
| staging `bbae334` | `uv run pytest -q` | 73 passed |
| branch, builder's tests | `uv run pytest -q` | 79 passed |
| branch, + QA gap tests | `uv run pytest -q` | 84 passed in 0.44s |

## AC2 · no re-derivation left in src/

```
$ grep -rn 'third = 4 if\|fifth = 6 if' src/   # exit 1, no output
$ grep -rn 'third = \|fifth = ' src/           # exit 1, no output
$ grep -rn 'HarmonyCandidate(' src/            # one site: analysis.py:356
```

## AC6 · arrangement output unchanged vs staging

Both fixtures × both relationships, `--seed 1`, `cmp` on the `.musicxml`:

```
IDENTICAL simple_melody support
IDENTICAL simple_melody countermelody
IDENTICAL minor_melody support
IDENTICAL minor_melody countermelody
```

Sidecar `.miare.json` checked too, since this change touches `to_jsonable`. For all four
runs the sidecar is identical except (a) the `output` path, which embeds the filename, and
(b) the additive `pcs` array per candidate. Nothing removed, nothing renumbered, no value
changed.

## Equivalence of the old derivation vs the carried set

All 12 tonics × 2 modes × 9 vocabulary degrees = 216 chords. The deleted
`else 3` / `else 7` expansion agreed with the vocabulary's own set in **216/216** cases.

So the refactor changes no behaviour today — it is de-duplication, and the defect it removes
is latent, not live. Worth stating plainly: the ticket's Risk section implies a quality could
already be mis-scored. It cannot, not with today's vocabulary. The value is that a fourth
quality added tomorrow can no longer fall through.

## Test gaps QA closed (written red-first)

Proved red against `bbae334` before being added: 4 of the 5 fail there
(`AttributeError: 'HarmonyCandidate' object has no attribute 'pcs'`, `DID NOT RAISE TypeError`,
`to_jsonable` returning the set unchanged).

| Gap | Test |
|---|---|
| `to_jsonable`'s new set branch was untested — the one line keeping `arrange --report` from `TypeError` | `TestChordPitchClassesSurviveSerialisation` |
| Sorting was untested, and it is what keeps the sidecar stable between runs | `test_to_jsonable_writes_a_set_as_a_sorted_list` |
| Builder's tests cover only the 3 qualities the fixture selects; degrees 8–9 (chromatic V/V, bVII, harmonic-minor V) were never asserted | `test_candidates_cover_every_vocabulary_degree` |
| `pcs` being required and keyword-only is the whole safety property, and nothing asserted it | `test_pcs_is_required_and_keyword_only` |

## Test gaps not written

- None at Medium or above.
