# Local CI. This repo deliberately has NO GitHub Actions workflow — CI runs here,
# on the machine, before a merge. `.github/workflows/ci.yml` was removed 2026-10-16;
# these targets are its replacement.
.PHONY: ci lint test ste ste-report

ci: lint test

# A HARD gate since 2026-10-22. The 81-finding backlog that once made this
# informational is gone (`ruff check .` exits 0), so a new finding is now a
# real regression and fails the build. Keep it that way: fix the finding, do
# not soften the gate.
lint:
	ruff check .

# The default suite is fast and hermetic: `slow`/`network` tests are opt-in (see
# tests/conftest.py) and tests needing a live ~/.sio DB or an LM auto-skip when
# absent. This is the drift guardrail.
test:
	pytest -p no:cacheprovider

# Prose quality (ASD-STE100 style) over a single file, via the `ste-lint` tool
# in ~/.local/bin. NOT wired into `ci`: the repo-wide baseline is 9.16
# violations per 100 verbs against a 2.0 target, so a gate would fail on day
# one for pre-existing prose. See docs/code-quality-baseline.md.
#   make ste FILE=src/sio/suggestions/confidence.py
ste:
	@test -n "$(FILE)" || { echo "usage: make ste FILE=<path>"; exit 2; }
	ste-lint "$(FILE)" --glossary ste-glossary.txt

# Gate ONE file at the target score — use this on files you touch.
ste-report:
	@test -n "$(FILE)" || { echo "usage: make ste-report FILE=<path>"; exit 2; }
	ste-lint "$(FILE)" --glossary ste-glossary.txt --max-score 2.0
