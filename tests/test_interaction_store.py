"""Tests for interaction_store.py — persistence behind a swappable backend.

Run from the project root:  .venv/bin/python tests/test_interaction_store.py

No database runs: MemoryBackend is a dict and JSONBackend writes to a tmpdir.
The load-bearing test is the ROUND TRIP — a reflection's sharing must survive
save/load, or a document schema has quietly destroyed it.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interaction_state import LamFan, subterm_at, type_in_context, type_of
from interaction_engine import Entity, Option, Proposal, QuerySession
from interaction_store import (
    InteractionStore, JSONBackend, MemoryBackend, QueryRecord,
    question_of, record_from_session, terms_of,
)

FAILED: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  {name:<48} OK")
    else:
        print(f"  {name:<48} FAIL  got={got!r} want={want!r}")
        FAILED.append(name)


def ok(name, cond):
    check(name, bool(cond), True)


A = Entity("a", "A", "first")
B = Entity("b", "B")
C = Entity("c", "C")
D = Entity("d", "D")


class Scripted:
    def __init__(self, seeds, proposals):
        self._seeds, self._proposals = seeds, list(proposals)

    def seeds(self, query):
        return self._seeds

    def propose(self, *a, **k):
        return self._proposals.pop(0) if self._proposals else Proposal(case=0)


def build_session(proposals, user="marek"):
    s = QuerySession("why does aspirin work", Scripted([("aspirin", A)], proposals),
                     user=user)
    s.start()
    s.open(A)
    return s


# ── a record from a session ───────────────────────────────────────────────
print("=== record_from_session ===")

p1 = Proposal(case=1, point="aspirin", question="which?", retype=C,
              options=[Option("to D", entity=D), Option("to B", entity=B)])
s = build_session([p1])
s.apply(s.propose(), p1.options[0])
s.close()

rec = record_from_session(s, answer="because COX.")
check("user recorded", rec.user, "marek")
check("query recorded", rec.query, "why does aspirin work")
check("one interaction", len(rec.interactions), 1)
check("answer recorded", rec.answer, "because COX.")
check("[t] recorded", rec.interactions[0]["type"], "d")
check("step recorded", rec.interactions[0]["steps"][0]["case"], 1)
check("point recorded", rec.interactions[0]["steps"][0]["point"], "aspirin")
ok("entity map has the seed", "a" in rec.entities)
ok("entity map has the answer", "d" in rec.entities)
check("seed label kept", rec.entities["a"]["label"], "A")
check("seed gloss kept", rec.entities["a"]["gloss"], "first")

# the lambdaList and the interaction question
s2 = build_session([])
s2.skip(Proposal(case=1, retype=C, options=[]))
s2.close()
rec2 = record_from_session(s2)
check("lambdaList recorded", rec2.lambda_list, ["c"])
ok("interaction question recorded", rec2.question is not None)
q = question_of(rec2)
check("question reads back as a binder", q.var.iri, "c")

# ── the round trip that matters ───────────────────────────────────────────
print("=== sharing survives save/load (case 3) ===")

p3 = Proposal(case=3, point="aspirin", question="a pair?",
              options=[Option("A/B", entity_a=A, entity_b=B),
                       Option("A/D", entity_a=A, entity_b=D)])
s3 = build_session([p3])
s3.apply(s3.propose(), p3.options[0])
s3.close()

rec3 = record_from_session(s3)
store = InteractionStore()
rid = store.save(rec3)
back = store.get(rid)
terms = terms_of(back)

t = terms[0]
ok("the fan survived", isinstance(t, LamFan))
ok("ONE shared subject after load",
   subterm_at(t, (0,)) is subterm_at(t, (1,)))
check("casts survived", (t.grey_cast, t.black_cast), ("a", "b"))
check("both occurrences still type as C (left)", type_of(subterm_at(t, (0,))), "a")

# a tree-shaped schema would have written the subject twice; assert it did not
raw = back.interactions[0]["term"]
check("subject stored once", raw["type"], "fan")
ok("principal is a single node in the document", "principal" in raw)

print("=== the retype survives save/load (cases 1, 2) ===")

rec1 = record_from_session(s)
store1 = InteractionStore()
t1 = terms_of(store1.get(store1.save(rec1)))[0]
check("[app(ta,td)] = D", type_of(t1), "d")
check("[ta] in this app = C", type_in_context(t1, (0,)), "c")
check("[ta] alone is still A", type_of(t1.func), "a")

print("=== entity reuse reloads as ONE node ===")

s4 = build_session([])
v1 = s4.entities.get("x", "X")
v2 = s4.entities.get("x", "X")
ok("one node while building", v1 is v2)
from interaction_state import LamApp
s4.current.term = LamApp(func=v1, arg=v2)
s4.close()
st4 = InteractionStore()
t4 = terms_of(st4.get(st4.save(record_from_session(s4))))[0]
ok("reused entity is one node after load", t4.func is t4.arg)

# ── the backends ──────────────────────────────────────────────────────────
print("=== MemoryBackend ===")

st = InteractionStore(MemoryBackend())
r1 = st.save(QueryRecord(user="marek", query="q1"))
r2 = st.save(QueryRecord(user="marek", query="q2"))
r3 = st.save(QueryRecord(user="someone", query="q3"))
check("ids are distinct", len({r1, r2, r3}), 3)
check("get by id", st.get(r1).query, "q1")
check("per user", [r.query for r in st.for_user("marek")], ["q2", "q1"])
check("other user separate", [r.query for r in st.for_user("someone")], ["q3"])
check("all", len(st.all()), 3)
check("unknown id", st.get("nope"), None)

print("=== JSONBackend: survives the process ===")

with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "queries.json"
    st1 = InteractionStore(JSONBackend(path))
    rid = st1.save(record_from_session(s3))
    # a NEW store over the same file
    st2 = InteractionStore(JSONBackend(path))
    got = st2.get(rid)
    ok("record survives a new store", got is not None)
    check("query survives", got.query, "why does aspirin work")
    t = terms_of(got)[0]
    ok("sharing survives the file too",
       subterm_at(t, (0,)) is subterm_at(t, (1,)))
    check("per user from file", [r.query for r in st2.for_user("marek")],
          ["why does aspirin work"])
    # Ordering must hold even when several records share a timestamp —
    # saved_at has second resolution, so same-second saves tie.
    st2.save(QueryRecord(user="u", query="first"))
    st2.save(QueryRecord(user="u", query="second"))
    st2.save(QueryRecord(user="u", query="third"))
    check("same-second ties keep insertion order",
          [r.query for r in st2.for_user("u")], ["third", "second", "first"])
    # saving an existing id replaces rather than duplicates
    before = len(st2.all())
    got.answer = "edited"
    st2.save(got)
    check("no duplicate on re-save", len(st2.all()), before)
    check("edit persisted", st2.get(rid).answer, "edited")

print("=== the store defaults to memory ===")
ok("default backend", isinstance(InteractionStore().backend, MemoryBackend))

# save_session is the whole path in one call
st = InteractionStore()
rec = st.save_session(s3, answer="an answer")
check("save_session records the answer", rec.answer, "an answer")
ok("and is retrievable", st.get(rec.id) is not None)

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
