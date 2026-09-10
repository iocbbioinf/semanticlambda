"""Persistence for browser sessions — what `resume` writes.

"resume for now means — save init question and all its readings."

So a record is one QUERY plus the readings built under it. Each reading is
stored closed: its term (sharing preserved), the step log that produced it, and
THE INTERACTIONS AS THE USER MET THEM — the point of the query at issue, the
question put, and the answer chosen. The term says what was built and `steps`
says by which reading steps, but only the interactions say what was ASKED; a
reading saved without them can be replayed and not read back.

The pointer set is deliberately absent — closing a reading is exactly what drops
it (reading_desc §1), and re-opening re-mints a single pointer at the root.

Terms go through `reading_state.lam_to_dict_shared`, which knows about sharing
fan-ins; the plain encoder in `kg_store` would lose a reflection.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from reading_state import lam_from_dict_shared, lam_to_dict_shared

SESSIONS_DB = Path(__file__).parent / "data" / "browser_sessions.json"


def _load_raw() -> list[dict]:
    if not SESSIONS_DB.exists():
        return []
    try:
        return json.loads(SESSIONS_DB.read_text())
    except json.JSONDecodeError:
        return []


def save_session(session) -> Path:
    """Append this session's query and its closed readings."""
    record = {
        "query": session.query,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "readings": [
            {
                "name": c.name,
                "seed": {"iri": c.seed.iri, "label": c.seed.label},
                "term": lam_to_dict_shared(c.term),
                "steps": c.steps,
                "interactions": [
                    {"kind": i.kind, "point": i.point, "question": i.question,
                     "answer": i.answer, "calculus": i.calculus,
                     "rationale": i.rationale}
                    for i in getattr(c, "interactions", [])
                ],
            }
            for c in session.closed
        ],
    }
    data = _load_raw()
    data.append(record)
    SESSIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_DB.write_text(json.dumps(data, indent=2))
    return SESSIONS_DB


def load_sessions() -> list[dict]:
    """Saved sessions, with terms decoded (sharing rebuilt)."""
    out = []
    for rec in _load_raw():
        readings = []
        for r in rec.get("readings", []):
            readings.append({
                "name": r.get("name", ""),
                "seed": r.get("seed", {}),
                "term": lam_from_dict_shared(r["term"]) if r.get("term") else None,
                "steps": r.get("steps", []),
                # Absent in records written before interactions were kept.
                "interactions": r.get("interactions", []),
            })
        out.append({"query": rec.get("query", ""),
                    "saved_at": rec.get("saved_at", ""),
                    "readings": readings})
    return out
