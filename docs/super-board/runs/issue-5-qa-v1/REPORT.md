# QA v1 — issue #5 (register mapping move)

Branch: `issue-5-instruments-register-mapping` @ `ee4952f`
Base: `staging` @ `230843f`

## AC1 — `generated_from_source` defined in `src/miare/instruments.py`, unchanged

`instruments.py:25-141`. Diffed the function body against the version removed from
`musicxml.py`: identical token-for-token (only its surrounding imports changed —
`Fraction`, `Event`, `GeneratedEvent`, `Pitch` added to `instruments.py`'s header).

## AC2 — `musicxml.py` no longer defines it; `generation.py` / `counterpoint.py` import from `.instruments`

- `grep -n generated_from_source src/miare/musicxml.py` -> no match.
- `generation.py:6` -- `from .instruments import generated_from_source, get_profile`
- `counterpoint.py:6` -- `from .instruments import generated_from_source, get_profile`

## AC3 — `tests/test_pipeline.py` imports from new home, import block split

`test_pipeline.py:11` -- `from miare.instruments import generated_from_source`, separate
from the `from miare.musicxml import (...)` block that still imports `_duration_notation`,
`parse_musicxml`, `validate_round_trip`, `write_arrangement`.

## AC4 — `uv run pytest -q` passes, same test count as staging

```
$ uv run pytest -q
84 passed in 0.48s
```

Ran the identical command on a `staging`@`230843f` worktree: also `84 passed`. Same count.

## AC5 — arrangement output byte-identical, both relationships

```
$ uv run miare arrange tests/fixtures/simple_melody.musicxml --relationship support --seed 1 -o /tmp/branch-support.musicxml
$ uv run miare arrange tests/fixtures/simple_melody.musicxml --relationship countermelody --seed 1 -o /tmp/branch-countermelody.musicxml
```

Same two commands run against a `staging`@`230843f` worktree, then `diff`'d the `.musicxml`
outputs only (the `.miare.json` sidecar embeds the absolute output path and differs between
any two runs, as the issue notes):

```
diff /tmp/staging-support.musicxml /tmp/branch-support.musicxml              -> no diff
diff /tmp/staging-countermelody.musicxml /tmp/branch-countermelody.musicxml  -> no diff
```

Both relationships byte-identical.

## Extra check — no dead imports introduced

`musicxml.py` still uses `Fraction`, `Event`, `Pitch`, `GeneratedEvent` elsewhere
(round-trip parse/write code), so removing `generated_from_source` left no unused imports
behind.

## Verdict

5/5 AC pass. No PR review threads open. No code changes needed — evidence only.

Local tests: `uv run pytest -q`
