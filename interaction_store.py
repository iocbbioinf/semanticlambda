"""Persistence — (query, interaction, interaction question), per user.

One record is one QUERY by one USER: the subqueries it decomposed into, each
closed interaction with the steps as the user met them, the INTERACTION QUESTION
`lam E1...lam En.(t)` built over the lambdaList, and the ENTITY MAPPING that
gives every iri in those terms a name.

THE BACKEND IS AN INTERFACE (`StoreBackend`), like the entity index:

    MemoryBackend   the default. A dict. No dependencies, no server; a test or a
                    dev checkout needs nothing running.
    JSONBackend     the same thing on disk, for a local session that outlives
                    the process.
    MongoBackend    production. Deferred import, so `pymongo` is needed only if
                    it is actually used.

TERMS ARE SHARING GRAPHS, NOT TREES. An interaction that reflected (case 3) has
one subterm reachable through two occurrences. A document schema that stored
terms tree-shaped would write that subterm twice and read back TWO nodes, which
is not the same interaction — the sharing case 3 exists to create would be
silently destroyed by the round trip. So terms go through
`interaction_state.lam_to_dict_shared` / `lam_from_dict_shared`, which store the
shared subject once, and a round-trip test guards it.

THE POINTER SET IS DELIBERATELY ABSENT. Closing an interaction is exactly what
drops it: (t, P) -> t. A saved record holds closed interactions only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Protocol

from interaction_state import lam_from_dict_shared, lam_to_dict_shared


# ── the record ────────────────────────────────────────────────────────────


@dataclass
class QueryRecord:
    """One query by one user, as saved.

        user            who asked (a typed name; there is no authentication)
        query           what they asked, verbatim
        interactions    the closed interactions, each with its term and steps
        question        the interaction question, lam E1...lam En.(t)
        lambda_list     the binders, in order — what nobody settled
        entities        iri -> {label, gloss}, so a term reads back with names
        answer          the model's response to (query + interaction)
    """
    user: str
    query: str
    interactions: list[dict] = field(default_factory=list)
    question: Optional[dict] = None
    lambda_list: list[str] = field(default_factory=list)
    entities: dict[str, dict] = field(default_factory=dict)
    answer: str = ""
    saved_at: str = ""
    id: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "user": self.user,
            "query": self.query,
            "interactions": self.interactions,
            "question": self.question,
            "lambda_list": self.lambda_list,
            "entities": self.entities,
            "answer": self.answer,
            "saved_at": self.saved_at or _now(),
        }
        if self.id:
            d["id"] = self.id
        return d

    @staticmethod
    def from_dict(d: dict) -> "QueryRecord":
        return QueryRecord(
            user=d.get("user", ""),
            query=d.get("query", ""),
            interactions=d.get("interactions", []),
            question=d.get("question"),
            lambda_list=d.get("lambda_list", []),
            entities=d.get("entities", {}),
            answer=d.get("answer", ""),
            saved_at=d.get("saved_at", ""),
            id=d.get("id"),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── building a record from a session ──────────────────────────────────────


def record_from_session(session, answer: str = "") -> QueryRecord:
    """Everything worth keeping about one finished query.

    The entity map is collected from the session registry rather than from the
    terms, so an entity that was proposed and then only bound — never placed in
    a term — still has its name saved.
    """
    interactions = [
        {
            "name": c.name,
            "seed": {"iri": c.seed.iri, "label": c.seed.label},
            "subquery": c.subquery,
            "term": lam_to_dict_shared(c.term),
            "type": c.type_iri(),
            "steps": [
                {"case": s.case, "point": s.point, "question": s.question,
                 "answer": s.answer, "calculus": s.calculus,
                 "rationale": s.rationale}
                for s in c.steps
            ],
        }
        for c in session.closed
    ]
    q = session.interaction_question()
    entities: dict[str, dict] = {}
    for e in session.seeds:
        entities[e.iri] = {"label": e.label, "gloss": e.gloss}
    for e in session.lambda_list:
        entities.setdefault(e.iri, {"label": e.label, "gloss": e.gloss})
    # Everything that reached a term is in the registry, including entities
    # proposed mid-interaction that were never a seed.
    for iri, var in getattr(session.entities, "_vars", {}).items():
        entities.setdefault(iri, {"label": var.label, "gloss": ""})

    return QueryRecord(
        user=session.user,
        query=session.query,
        interactions=interactions,
        question=lam_to_dict_shared(q) if q is not None else None,
        lambda_list=[e.iri for e in session.lambda_list],
        entities=entities,
        answer=answer,
        saved_at=_now(),
    )


def terms_of(record: QueryRecord) -> list:
    """The closed interactions' terms, rebuilt with sharing restored.

    One registry across the whole record, so an entity reused between
    interactions reads back as ONE node — as it was when built.
    """
    from interaction_state import EntityRegistry
    reg = EntityRegistry()
    for iri, meta in record.entities.items():
        reg.get(iri, meta.get("label", iri))
    return [lam_from_dict_shared(i["term"], reg) for i in record.interactions]


def question_of(record: QueryRecord):
    """The interaction question, rebuilt with sharing restored."""
    if record.question is None:
        return None
    from interaction_state import EntityRegistry
    reg = EntityRegistry()
    for iri, meta in record.entities.items():
        reg.get(iri, meta.get("label", iri))
    return lam_from_dict_shared(record.question, reg)


# ── the interface ─────────────────────────────────────────────────────────


class StoreBackend(Protocol):
    """Where records live."""

    def save(self, record: QueryRecord) -> str:
        """Persist, returning the record's id."""

    def get(self, record_id: str) -> Optional[QueryRecord]:
        ...

    def for_user(self, user: str, limit: int = 50) -> list[QueryRecord]:
        """That user's records, newest first."""

    def all(self, limit: int = 200) -> list[QueryRecord]:
        ...


# ── backends ──────────────────────────────────────────────────────────────


class MemoryBackend:
    """A dict. The default: nothing to run, nothing to clean up."""

    def __init__(self) -> None:
        self._records: dict[str, QueryRecord] = {}
        self._order: dict[str, int] = {}
        self._seq = 0

    def save(self, record: QueryRecord) -> str:
        if not record.id:
            self._seq += 1
            record.id = f"r{self._seq}"
        if not record.saved_at:
            record.saved_at = _now()
        self._records[record.id] = record
        self._order.setdefault(record.id, len(self._order))
        return record.id

    def _newest_first(self, rs: list[QueryRecord]) -> list[QueryRecord]:
        """Newest first, ties broken by INSERTION ORDER.

        `saved_at` has second resolution, so two records saved in the same
        second tie — which without this would leave a user's history in
        arbitrary order.
        """
        return sorted(rs, key=lambda r: (r.saved_at, self._order.get(r.id, 0)),
                      reverse=True)

    def get(self, record_id: str) -> Optional[QueryRecord]:
        return self._records.get(record_id)

    def for_user(self, user: str, limit: int = 50) -> list[QueryRecord]:
        rs = [r for r in self._records.values() if r.user == user]
        return self._newest_first(rs)[:limit]

    def all(self, limit: int = 200) -> list[QueryRecord]:
        return self._newest_first(list(self._records.values()))[:limit]


class JSONBackend:
    """One JSON file. For a local session that should outlive the process.

    Read-modify-write of the whole file, so it is single-process only: two
    writers would clobber one another. That is fine for local development and
    is why production uses Mongo.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return []

    def _write(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(rows, indent=2))

    def save(self, record: QueryRecord) -> str:
        rows = self._load()
        if not record.id:
            record.id = f"r{len(rows) + 1}"
        if not record.saved_at:
            record.saved_at = _now()
        rows = [r for r in rows if r.get("id") != record.id]
        rows.append(record.to_dict())
        self._write(rows)
        return record.id

    def get(self, record_id: str) -> Optional[QueryRecord]:
        for r in self._load():
            if r.get("id") == record_id:
                return QueryRecord.from_dict(r)
        return None

    @staticmethod
    def _newest_first(rows: list[dict]) -> list[QueryRecord]:
        """Newest first, ties broken by POSITION IN THE FILE.

        `saved_at` has second resolution, so records written in the same second
        tie; file order is the order they were saved in.
        """
        indexed = list(enumerate(rows))
        indexed.sort(key=lambda p: (p[1].get("saved_at", ""), p[0]),
                     reverse=True)
        return [QueryRecord.from_dict(r) for _, r in indexed]

    def for_user(self, user: str, limit: int = 50) -> list[QueryRecord]:
        rows = [r for r in self._load() if r.get("user") == user]
        return self._newest_first(rows)[:limit]

    def all(self, limit: int = 200) -> list[QueryRecord]:
        return self._newest_first(self._load())[:limit]


class MongoBackend:
    """Production. `pymongo` is imported here, so it is needed only if used."""

    def __init__(self, url: str = "mongodb://localhost:27017",
                 database: str = "semanticlambda",
                 collection: str = "queries") -> None:
        from pymongo import ASCENDING, DESCENDING, MongoClient

        self._client = MongoClient(url)
        self._col = self._client[database][collection]
        self._DESC = DESCENDING
        # Records are read back per user, newest first.
        self._col.create_index([("user", ASCENDING), ("saved_at", DESCENDING)])

    @staticmethod
    def _out(doc: dict) -> QueryRecord:
        doc = dict(doc)
        oid = doc.pop("_id", None)
        if oid is not None and not doc.get("id"):
            doc["id"] = str(oid)
        return QueryRecord.from_dict(doc)

    def save(self, record: QueryRecord) -> str:
        if not record.saved_at:
            record.saved_at = _now()
        doc = record.to_dict()
        if record.id:
            self._col.replace_one({"id": record.id}, doc, upsert=True)
            return record.id
        res = self._col.insert_one(doc)
        record.id = str(res.inserted_id)
        self._col.update_one({"_id": res.inserted_id},
                             {"$set": {"id": record.id}})
        return record.id

    def get(self, record_id: str) -> Optional[QueryRecord]:
        doc = self._col.find_one({"id": record_id})
        return self._out(doc) if doc else None

    def for_user(self, user: str, limit: int = 50) -> list[QueryRecord]:
        cur = self._col.find({"user": user}).sort("saved_at", self._DESC).limit(limit)
        return [self._out(d) for d in cur]

    def all(self, limit: int = 200) -> list[QueryRecord]:
        cur = self._col.find().sort("saved_at", self._DESC).limit(limit)
        return [self._out(d) for d in cur]


# ── the store ─────────────────────────────────────────────────────────────


class InteractionStore:
    """What the app talks to. The backend is an implementation detail."""

    def __init__(self, backend: Optional[StoreBackend] = None) -> None:
        self.backend = backend if backend is not None else MemoryBackend()

    def save_session(self, session, answer: str = "") -> QueryRecord:
        """Save one finished query: its interactions, question and entities."""
        rec = record_from_session(session, answer=answer)
        self.backend.save(rec)
        return rec

    def save(self, record: QueryRecord) -> str:
        return self.backend.save(record)

    def get(self, record_id: str) -> Optional[QueryRecord]:
        return self.backend.get(record_id)

    def for_user(self, user: str, limit: int = 50) -> list[QueryRecord]:
        return self.backend.for_user(user, limit=limit)

    def all(self, limit: int = 200) -> list[QueryRecord]:
        return self.backend.all(limit=limit)
