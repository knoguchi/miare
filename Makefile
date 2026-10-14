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

viewer:  ## serve score_viewer.html at $(PORT) — open /score_viewer.html
	@echo "→ http://localhost:$(PORT)/score_viewer.html   (ctrl-c to stop)"
	@uv run python -m http.server $(PORT)

samples:  ## regenerate samples/ from each sample's source.musicxml
	@for dir in samples/*/; do \
	  name=$$(basename $$dir); \
	  [ -f "$$dir/source.musicxml" ] || continue; \
	  for rel in support countermelody; do \
	    echo "  $$name · $$rel"; \
	    uv run miare arrange "$$dir/source.musicxml" -o "$$dir/$$rel.musicxml" \
	      --relationship $$rel --seed 1 >/dev/null; \
	  done; \
	done

.PHONY: help install test lint viewer samples
