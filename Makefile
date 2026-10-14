# miare — common tasks. `make` on its own lists them.
.DEFAULT_GOAL := help
PORT ?= 8765

help:  ## list these targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk -F':.*?## ' '{printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## sync the virtualenv from uv.lock
	uv sync

test:  ## run the test suite
	uv run pytest -q

# Scoped to the dead-code rules issue #1 settled on. The repo has no [tool.ruff]
# section, so ruff's full default set reports ~45 style findings here — adopting
# those is a separate house-style decision, not this target's job.
# Never pass a bare `.`: it scans vendored skills under .claude/.
lint:  ## ruff dead-code check over our own sources
	uv run --with ruff ruff check src tests --select F401,F841

# `miare serve`, not `python -m http.server`: it serves the same files the same way and
# adds the analyze/arrange API, so a score dropped on the page can actually be arranged.
viewer:  ## serve score_viewer.html + the arrange API at $(PORT)
	@uv run miare serve --port $(PORT)

# Delegates rather than repeating the commands: the script pins the seed, the beam
# width and each sample's instrument profiles, and tests/test_samples.py regenerates
# through it to prove the committed samples are current. A second copy of those
# flags here would drift and fail that check.
samples:  ## regenerate samples/ (after any arranger change)
	@./scripts/make-samples.sh

.PHONY: help install test lint viewer samples
