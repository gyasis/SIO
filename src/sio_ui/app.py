"""FastAPI entrypoint for sio_ui.

Run with:
    uvicorn sio_ui.app:app --reload --port 8770
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sio_ui.db import curator, sio_ro

app = FastAPI(title="SIO Data Explorer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tables exposed to the UI. Add cautiously — anything here is queryable.
_ALLOWED_TABLES = {
    "error_records",
    "positive_records",
    "flow_events",
    "patterns",
    "datasets",
    "suggestions",
    "optimized_modules",
    "optimization_runs",
    "velocity_snapshots",
    "processed_sessions",
}


@app.get("/api/health")
def health() -> dict[str, Any]:
    with sio_ro() as ro:
        n = ro.execute("SELECT COUNT(*) FROM error_records").fetchone()[0]
    return {"ok": True, "error_records": n}


@app.get("/api/tables")
def list_tables() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with sio_ro() as ro:
        for t in sorted(_ALLOWED_TABLES):
            try:
                count = ro.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                count = None
            out.append({"name": t, "row_count": count})
    return out


@app.get("/api/tables/{table}/rows")
def list_rows(
    table: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_hidden: bool = Query(False),
) -> dict[str, Any]:
    if table not in _ALLOWED_TABLES:
        raise HTTPException(404, f"table not exposed: {table}")

    with sio_ro() as ro:
        rows = ro.execute(
            f"SELECT * FROM {table} ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = ro.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # Decorate each row with curator badges
    with curator() as cur:
        out = []
        for r in rows:
            row_id = r["id"] if "id" in r.keys() else None
            badges = _badges_for(cur, table, row_id) if row_id else {}
            if not include_hidden and badges.get("hidden"):
                continue
            out.append({**dict(r), "_curator": badges})

    return {"table": table, "total": total, "limit": limit, "offset": offset, "rows": out}


def _badges_for(cur, table: str, row_id: int) -> dict[str, Any]:
    actions = cur.execute(
        "SELECT action, reason FROM curator_actions WHERE table_name=? AND row_id=?",
        (table, row_id),
    ).fetchall()
    notes = cur.execute(
        "SELECT COUNT(*) FROM curator_notes WHERE table_name=? AND row_id=?",
        (table, row_id),
    ).fetchone()[0]
    badges: dict[str, Any] = {"notes": notes}
    for a in actions:
        badges[a["action"]] = a["reason"] or True
    return badges


# ----- Curator write endpoints -----

class NoteIn(BaseModel):
    note: str


class ActionIn(BaseModel):
    action: str  # 'hidden' | 'flagged' | 'starred' | 'approved'
    reason: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.post("/api/tables/{table}/rows/{row_id}/notes")
def add_note(table: str, row_id: int, payload: NoteIn) -> dict[str, Any]:
    if table not in _ALLOWED_TABLES:
        raise HTTPException(404, "table not exposed")
    with curator() as cur:
        cur.execute(
            "INSERT INTO curator_notes(table_name,row_id,note,created_at) "
            "VALUES(?,?,?,?)",
            (table, row_id, payload.note, _now()),
        )
        cur.commit()
    return {"ok": True}


@app.get("/api/tables/{table}/rows/{row_id}/notes")
def list_notes(table: str, row_id: int) -> list[dict[str, Any]]:
    with curator() as cur:
        rows = cur.execute(
            "SELECT id, note, created_at, created_by FROM curator_notes "
            "WHERE table_name=? AND row_id=? ORDER BY id DESC",
            (table, row_id),
        ).fetchall()
    return [dict(r) for r in rows]


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int) -> dict[str, bool]:
    with curator() as cur:
        cur.execute("DELETE FROM curator_notes WHERE id=?", (note_id,))
        cur.commit()
    return {"ok": True}


@app.post("/api/tables/{table}/rows/{row_id}/actions")
def add_action(table: str, row_id: int, payload: ActionIn) -> dict[str, bool]:
    if table not in _ALLOWED_TABLES:
        raise HTTPException(404, "table not exposed")
    if payload.action not in {"hidden", "flagged", "starred", "approved"}:
        raise HTTPException(400, "bad action")
    with curator() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO curator_actions"
            "(table_name,row_id,action,reason,created_at) VALUES(?,?,?,?,?)",
            (table, row_id, payload.action, payload.reason, _now()),
        )
        cur.commit()
    return {"ok": True}


@app.delete("/api/tables/{table}/rows/{row_id}/actions/{action}")
def remove_action(table: str, row_id: int, action: str) -> dict[str, bool]:
    with curator() as cur:
        cur.execute(
            "DELETE FROM curator_actions WHERE table_name=? AND row_id=? AND action=?",
            (table, row_id, action),
        )
        cur.commit()
    return {"ok": True}
