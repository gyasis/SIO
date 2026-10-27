# Operating SIO — what to run, and when

The other docs list every command. This one answers a different question: **given the state
SIO is in right now, what is the next thing to run?**

Read [`SIO_PHILOSOPHY.md`](SIO_PHILOSOPHY.md) first if you have not. The short version
governs everything below: SIO detects and generates automatically; **you apply**. A stage
that has not run is usually a stage nobody asked for, not a broken stage.

---

## 1. The pipeline, in order

Each stage consumes what the stage above it produced. Running stage 5 when stage 3 is 20
days stale gives you 20-day-old answers, not an error.

```
  1  CAPTURE      hooks write every tool call      automatic, live
        |              ~/.sio/<platform>/behavior_invocations.db
        v
  2  SYNC         copy into the canonical DB       automatic (Stop hook) + `sio sync`
        |              ~/.sio/sio.db
        v
  3  MINE         transcripts -> errors/positives  automatic (sio-mine-daily.timer)
        |              error_records, positive_records, processed_sessions
        v
  4  FLOWS        recurring tool sequences         MANUAL  `sio flows`
        |              flow_events
        v
  5  PATTERNS     cluster errors into patterns     MANUAL  `sio discover`
        |              patterns
        v
  6  SUGGEST      patterns -> proposed rules       MANUAL  `sio suggest`   (costs ~$0.05)
        |              suggestions (status=pending)
        v
  7  REVIEW       you read them                    MANUAL  `sio suggest-review`
        |
        v
  8  APPLY        approved rule lands in config    MANUAL  `sio apply <id>`
        |              CLAUDE.md / hooks / skills
        v
  9  MEASURE      did the rule shrink errors?      MANUAL  `sio velocity`
                       needs ~2 weeks of data after APPLY
```

**Stages 1-3 run themselves. Stages 4-9 never run unless you ask.** That is deliberate: a
rule that edits your instruction files is a decision, not a cron job.

---

## 2. Where am I right now?

```bash
sio status
```

That is the intended answer and it covers stages 1-3. For the whole chain, including the
manual stages, this read-only probe prints one line per stage:

```bash
python3 - <<'EOF'
import sqlite3, os, datetime
sio  = os.path.expanduser('~/.sio/sio.db')
plat = os.path.expanduser('~/.sio/claude-code/behavior_invocations.db')
c = sqlite3.connect(f'file:{sio}?mode=ro',  uri=True)
p = sqlite3.connect(f'file:{plat}?mode=ro', uri=True)
now = datetime.datetime.now(datetime.timezone.utc)
def one(conn, q, d=0):
    try: return conn.execute(q).fetchone()[0] or d
    except Exception: return d
def age(ts):
    if not ts: return 'never'
    try: return f"{(now - datetime.datetime.fromisoformat(str(ts).replace('Z','+00:00'))).days}d ago"
    except Exception: return '?'
print(f"1 capture      {one(p,'select count(*) from behavior_invocations'):,} rows, newest {age(one(p,'select max(timestamp) from behavior_invocations',None))}")
print(f"2 sync         canonical {one(c,'select count(*) from behavior_invocations'):,} / per-platform {one(p,'select count(*) from behavior_invocations'):,}")
print(f"3 mine         {one(c,'select count(*) from error_records'):,} errors, last {age(one(c,'select max(mined_at) from processed_sessions',None))}")
print(f"4 flows        {one(c,'select count(*) from flow_events'):,} events, last {age(one(c,'select max(mined_at) from flow_events',None))}")
print(f"5 patterns     {one(c,'select count(*) from patterns'):,}, last {age(one(c,'select max(created_at) from patterns',None))}")
pending = one(c, "select count(*) from suggestions where status='pending'")
print(f"6 suggestions  {one(c,'select count(*) from suggestions'):,} total, {pending} pending")
print(f"8 applied      {one(c,'select count(*) from applied_changes'):,}")
EOF
```

---

## 3. What to run, by what the probe says

Work down this table and stop at the first row that matches. Each row's command is cheap
and read-only unless the cost column says otherwise.

| If the probe shows | Run | Cost |
|---|---|---|
| canonical far below per-platform | `sio sync` | free |
| mine older than ~2 days | `sio mine --recent 7` | free |
| pending suggestions > 0 | `sio suggest-review` | free |
| flows stale, mine fresh | `sio flows` | free |
| patterns stale, mine fresh | `sio discover` | free |
| patterns fresh, no new suggestions | `sio suggest` | ~$0.05 |
| applied > 0 and 2 weeks passed | `sio velocity` | free |
| none of the above | nothing — the loop is current | — |

**Always run `sio suggest-review` before `sio suggest`.** Generating more proposals while
old ones sit unread costs money and buries the good ones.

---

## 4. A reasonable cadence

Nothing here is mandatory. Skipping a week costs you nothing except later answers.

- **Session start** — `sio briefing`. Free, and it surfaces violations and pending counts.
- **Weekly, ~5 minutes** — run the probe. If suggestions are pending, `sio suggest-review`.
- **After a rough week** — `sio flows` then `sio discover`, then `sio suggest` if the
  patterns look new. This is the sequence that turns friction into rules.
- **Two weeks after applying anything** — `sio velocity`. Earlier than that reads noise.
- **When you want to know "have I hit this before?"** — `sio search "<topic>"`. This needs
  no pipeline stage at all; it reads transcripts directly.

---

## 5. Traps worth knowing

**A green `sio status` is not proof the corpus is whole.** Until 2026-10-27 the drift
check flagged a gap only past 5%. At 35k rows that hid about 1,770 missing tool calls
behind a check mark. A sync that had never copied a single row still read "in sync".
Status now prints the exact row gap. On an older build, compare the counts yourself:

```bash
sqlite3 ~/.sio/sio.db                              "select count(*) from behavior_invocations"
sqlite3 ~/.sio/claude-code/behavior_invocations.db "select count(*) from behavior_invocations"
```

**Canonical larger than per-platform is normal.** The per-platform database is a rolling
capture buffer with a retention purge. The canonical database is the durable corpus, so it
keeps rows the buffer has dropped. Only the other direction is a gap.

**"No new suggestions" usually means stage 5 never ran.** Suggestions come from patterns,
patterns come from `sio discover`, and `discover` is manual. Check the age of `patterns`
before concluding that SIO found nothing.

**Each machine's SIO is its own installation.** Databases do not merge across machines,
and nothing syncs them. A second machine needs its own `sio mine` to have a corpus at all.

**Every row is only as good as its labels.** Before 2026-10-27 the capture hook read a
payload key Claude Code does not send. Every row therefore recorded "no output, no error",
and the corpus asserted that 99.99% of tool calls produced nothing. Rows captured before
that fix carry labels you cannot trust. `sio status` cannot tell you this. The test is
whether `correct_outcome` varies:

```bash
sqlite3 ~/.sio/claude-code/behavior_invocations.db \
  "select correct_outcome, count(*) from behavior_invocations group by 1"
```

One group means the labels are degenerate. Two or more means capture is working.

---

## 6. Related

- [`SIO_PHILOSOPHY.md`](SIO_PHILOSOPHY.md) — why stages 4-9 are manual, and the list of
  things SIO deliberately refuses to automate
- [`user-guide.md`](user-guide.md) — every flag of every command
- [`troubleshooting.md`](troubleshooting.md) — failure modes and their causes
- [`architecture.md`](architecture.md) — the database layout the probe reads
