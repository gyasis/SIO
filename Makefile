# Local CI. This repo deliberately has NO GitHub Actions workflow — CI runs here,
# on the machine, before a merge. `.github/workflows/ci.yml` was removed 2026-10-16;
# this target is its replacement, running the same two steps it ran.
.PHONY: ci lint lint-strict test

ci: lint test

# Informational, exactly as the deleted workflow had it: the repo carries a
# pre-existing lint backlog (81 findings on 2026-10-16), so a hard gate here
# would fail for reasons unrelated to the change in hand. Keep the files you
# TOUCH clean. When `ruff check .` finally comes back empty, make `ci` depend on
# `lint-strict` instead and delete this target.
lint:
	-ruff check .

lint-strict:
	ruff check .

# The default suite is fast and hermetic: `slow`/`network` tests are opt-in (see
# tests/conftest.py) and tests needing a live ~/.sio DB or an LM auto-skip when
# absent. This is the drift guardrail.
test:
	pytest -p no:cacheprovider
