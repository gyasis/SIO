# Code quality baseline — ruff and STE prose, 2026-10-22

Two sweeps ran over this repo on 2026-10-22. This file records what each one
found, so the next session reads the numbers instead of deriving them again.

## 1. ruff: 81 findings to 0

`ruff check .` had 81 findings. It now exits 0, and `make ci` gates on it.

| rule | count | what happened |
|---|---:|---|
| I001 unsorted-imports | 41 | ruff `--fix` sorted them |
| F401 unused-import | 16 | 15 by `--fix`; 1 dead `tqdm` alias by hand |
| F841 unused-variable | 9 | 7 were stray; 2 got the missing assertion |
| E501 line-too-long | 8 | wrapped by hand, all were 100 to 117 chars |
| E702 semicolon statements | 4 | split onto their own lines |
| E402 import not at top | 1 | merged into the top import block |
| F811 redefinition | 1 | **a real bug, see below** |
| F821 undefined-name | 1 | **a real bug, see below** |

### Two findings were real bugs, not style

**`src/sio/core/dspy/datasets.py` — infinite recursion.** Line 21 imported
`db_path as _default_db_path`. Line 50 then defined a function with that same
name, whose body called `_default_db_path()`. The def shadowed the import, so
the function called itself. Every `load_gold_standards()` call that omitted
`db_path` raised `RecursionError`. The fix deletes the shadowing def.

**`src/sio/mining/specstory_parser.py` — a failure handler that failed.** The
handler for a malformed JSON block read `str(file_path)`, but
`_extract_md_tool_calls()` takes only the segment text. One bad JSON block
raised `NameError` and killed the parse of the whole file. The sibling handler
30 lines up already solved this, and its comment names the same class of bug
from v0.1.4. The fix copies that pattern and logs a stable key.

Both bugs now have a reproduction on record. Neither had a test.

### The nine unused variables split two ways

Seven held a value nobody read, so the call stays and the assignment goes.
Two marked a test that stopped half-way:

- `tests/unit/arena/test_promote_to_gold.py` captured `gold_id_2` and never
  checked it, though its own comment states the contract. The test now asserts
  that a duplicate promote returns `None` or the first id.
- `tests/unit/suggest/test_suggest_cascade_characterization.py` threw away two
  `CliRunner` results. Both tests now assert `result.exit_code == 0`.

Three of these edits needed a line number, not a text match. The same line
appears twice in the file, and only one copy is dead. The live copy asserts
what the dead copy dropped. Copy-paste built these tests, and the copy lost
the assertion.

## 2. STE prose baseline

`ste-lint` scores prose against an ASD-STE100-style rule set. The target is
under 2.0 violations per 100 verbs. The sweep read 615 files and scored 567 of
them; the other 48 hold no prose (empty `__init__.py` files).

| group | files | words | violations | per 100 verbs | files at/over 2.0 |
|---|---:|---:|---:|---:|---:|
| docs + skills (`.md`) | 85 | 76,515 | 1128 | **8.54** | 77 |
| `src/` + `scripts/` prose | 210 | 26,368 | 479 | **11.31** | 127 |
| `tests/` prose | 197 | 17,400 | 304 | **8.88** | 123 |
| `specs/` (historical) | 69 | 72,993 | 1527 | **11.74** | 66 |
| third-party papers (excluded) | 6 | 85,620 | 2061 | **24.36** | 6 |
| **first-party total (A+B+C)** | **492** | **120,283** | **1911** | **9.16** | **327** |


### Third-party text skews the raw number

`research/papers/` holds six downloaded papers and their PDFs. They score
24.36 and carry nearly every marketing word in the sweep. Two such words
appear 19 times across those papers and zero times in SIO's own prose.
Nobody rewrites another author's paper, so they sit outside the baseline.
Count them in and the repo reads worse than it writes.

First-party prose carries 14 such words in total. Most sit in `PRD.md`. The
real first-party problem is passive voice and long sentences, not vocabulary.

### The worst first-party files

| score | words | file |
|---:|---:|---|
| 83.3 | 185 | `src/sio/suggestions/confidence.py` |
| 46.1 | 126 | `src/sio/core/datasets/registry.py` |
| 40.0 | 192 | `src/sio/harnesses/codex.py` |
| 33.3 | 261 | `src/sio/harnesses/pi.py` |
| 28.8 | 376 | `tests/unit/suggest/test_suggest_cascade_characterization.py` |
| 28.0 | 373 | `src/sio/amplify.py` |
| 27.3 | 133 | `tests/unit/core/test_wuphf_agent.py` |
| 25.0 | 165 | `scripts/autopsy.py` |
| 25.0 | 167 | `src/sio/harnesses/__init__.py` |
| 23.8 | 279 | `src/sio/harnesses/opencode.py` |

Read the top score with care. `confidence.py` scored 83.3 on 185 words
because `ste-lint` counted only 6 verbs there, and the tool documents a ±15%
error on that count. Its per-100-words figure was 2.70. On a short docstring,
trust the per-word number and the direction of travel, not the absolute score.

That file is also the worked example. Rewriting its docstring in active
voice took it from 83.3 to 0.00, and changed no technical fact.

### The glossary

`ste-glossary.txt` holds 34 terms that `ste-lint` must never flag. A term
earns a place only as a real name in this project. Two entries matter most:

- `rich` is the Rich terminal library, not an adjective. It alone accounted
  for 53 false hits.
- `error` and `failure` name different things here. `error_records` is a mined
  table; `log_failure` is the observability sink. `ste-lint` reads the pair as
  one concept under two names, so the glossary shields both.

The glossary removed 206 findings, or 4% of the raw total.

## 3. How to run both again

```bash
make ci                                      # ruff (hard gate) + the suite
make ste FILE=src/sio/suggestions/confidence.py        # score one file
make ste-report FILE=<path>                  # exit 1 if that file is over 2.0
```

`ste-lint` is not in `make ci`. The first-party baseline is 9.16 against a 2.0
target, so a repo-wide gate would fail on day one for prose nobody is touching.
Score the files you edit instead.

## 4. What this sweep did NOT do

327 first-party files still score at or over 2.0. This file fixed one of them
as a worked example and left the rest. A full rewrite touches 120,283 words of
prose, so it needs its own decision and its own pass.
