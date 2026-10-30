"""schema 008 — the IDENTITY tables agree on `agent:native_id`. The DEDUP key stays put.

Measured on razer 2026-10-30. `behavior_invocations` held 230/230 canonical ids.
`flow_events` held 357/357 bare. `error_records` held 193 bare against 1,578
canonical. `session_metrics` held 2,842/2,842 in a fourth form entirely:
`<file_path>:<sha256[:16]>`.

That fourth form is NOT an identity. It sits under `session_id TEXT NOT NULL
UNIQUE` and stops `sio mine` re-counting a transcript it already read. Rewriting
it to a handle drops re-mine idempotence, and raises nothing when it does. The
guard test below matters here as much as the backfill does.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sio.core.db.schema import init_db, migrate_008_canonical_session_ids
from sio.mining.flow_pipeline import _session_id_from_path

# A real session_metrics key, as mining/pipeline.py:692 builds it.
_DEDUP_KEY = (
    "/home/u/.claude/projects/-home-u-code/"
    "c6bb8d51-b45e-431e-a510-a426d8cc9286.jsonl:71623fff33d1ec13"
)


def _seed(db_path: str) -> None:
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO flow_events (session_id, flow_hash, sequence, ngram_size, "
        "timestamp, mined_at) VALUES (?,?,?,?,?,?)",
        ("bare-uuid-1", "h1", "Read>Edit", 2, "2026-10-30T00:00:00Z", "2026-10-30T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO flow_events (session_id, flow_hash, sequence, ngram_size, "
        "timestamp, mined_at) VALUES (?,?,?,?,?,?)",
        ("claude:already", "h2", "Read>Bash", 2, "2026-10-30T00:00:00Z", "2026-10-30T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO error_records (session_id, timestamp, source_type, source_file, "
        "error_text, mined_at) VALUES (?,?,?,?,?,?)",
        ("bare-uuid-2", "2026-10-30T00:00:00Z", "jsonl", "f.jsonl", "boom",
         "2026-10-30T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO session_metrics (session_id, file_path, mined_at) VALUES (?,?,?)",
        (_DEDUP_KEY, "/home/u/x.jsonl", "2026-10-30T00:00:00Z"),
    )
    conn.commit()
    conn.close()


def test_bare_ids_become_canonical(tmp_path: Path):
    db = str(tmp_path / "t.db")
    _seed(db)
    assert migrate_008_canonical_session_ids(db) == 2

    conn = sqlite3.connect(db)
    flows = {r[0] for r in conn.execute("SELECT session_id FROM flow_events")}
    errs = {r[0] for r in conn.execute("SELECT session_id FROM error_records")}
    conn.close()

    assert flows == {"claude:bare-uuid-1", "claude:already"}, "bare rewritten, canonical untouched"
    assert errs == {"claude:bare-uuid-2"}


def test_session_metrics_dedup_key_is_never_rewritten(tmp_path: Path):
    """The guard. Its UNIQUE key is what makes `sio mine` idempotent."""
    db = str(tmp_path / "t.db")
    _seed(db)
    migrate_008_canonical_session_ids(db)

    conn = sqlite3.connect(db)
    got = conn.execute("SELECT session_id FROM session_metrics").fetchone()[0]
    conn.close()
    assert got == _DEDUP_KEY, "session_metrics carries a dedup key, not an identity"


def test_idempotent(tmp_path: Path):
    db = str(tmp_path / "t.db")
    _seed(db)
    assert migrate_008_canonical_session_ids(db) == 2
    assert migrate_008_canonical_session_ids(db) == 0, "a second run must rewrite nothing"


def test_missing_table_is_skipped(tmp_path: Path):
    """The per-platform hook DB carries only behavior_invocations."""
    db = str(tmp_path / "partial.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE behavior_invocations (id INTEGER PRIMARY KEY, session_id TEXT)")
    conn.commit()
    conn.close()
    assert migrate_008_canonical_session_ids(db) == 0


def test_flow_writer_stores_the_agent_not_a_bare_stem():
    """The latent bug: a stem cannot say WHICH agent produced it."""
    claude = "/home/u/.claude/projects/-home-u-code/abc-123.jsonl"
    assert _session_id_from_path(Path(claude)) == "claude:abc-123"

    # Same stem, different agent -- these used to collide into one id.
    goose = "/home/u/.local/share/goose/sessions/abc-123.jsonl"
    assert _session_id_from_path(Path(goose)) == "goose:abc-123"
    assert _session_id_from_path(Path(claude)) != _session_id_from_path(Path(goose))
