"""Tests for interaction_engine.py — the loop, headless.

Run from the project root:  .venv/bin/python tests/test_interaction_engine.py

No model is called: a scripted StepSource stands in for the delegate, so the
whole loop runs for free.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimal_lambda import LamAbs, LamVar
from interaction_state import LamApp, LamFan, subterm_at, type_in_context, type_of
from interaction_engine import (
    Entity, Interaction, Option, Proposal, QuerySession,
)

FAILED: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  {name:<46} OK")
    else:
        print(f"  {name:<46} FAIL  got={got!r} want={want!r}")
        FAILED.append(name)


def ok(name, cond):
    check(name, bool(cond), True)


A = Entity("a", "A")
B = Entity("b", "B")
C = Entity("c", "C")
D = Entity("d", "D")
E = Entity("e", "E")


class Scripted:
    """A StepSource that hands out a fixed list of proposals."""

    def __init__(self, seeds, proposals):
        self._seeds = seeds
        self._proposals = list(proposals)
        self.closed_seen = []

    def seeds(self, query):
        return self._seeds

    def propose(self, query, subquery, term, here, closed):
        self.closed_seen.append(list(closed))
        if not self._proposals:
            return Proposal(case=0)
        return self._proposals.pop(0)


def sess(seeds, proposals, **kw):
    s = QuerySession("q", Scripted(seeds, proposals), **kw)
    s.start()
    return s


# ── decomposition ─────────────────────────────────────────────────────────
print("=== decompose query into subqueries ===")

s = sess([("first bit", A), ("second bit", B)], [])
check("two subqueries", [e.iri for e in s.seeds], ["a", "b"])
check("quotes kept", s.seed_quotes["a"], "first bit")
check("next subquery", s.next_subquery().iri, "a")
s.open(A)
check("started one", s.next_subquery().iri, "b")
check("term opens as the seed entity", type_of(s.current.term), "a")
check("one pointer at the root", len(s.current.pointers), 1)

# a repeated entity is one subquery
s2 = sess([("x", A), ("y", A)], [])
check("repeated entity deduped", len(s2.seeds), 1)

# ── case 1 ────────────────────────────────────────────────────────────────
print("=== case 1: app(ta,td), user moves to D ===")

p1 = Proposal(case=1, point="the bit", question="which?",
              retype=C, options=[Option("to D", entity=D),
                                 Option("to E", entity=E)])
s = sess([("the bit", A)], [p1])
s.open(A)
prop = s.propose()
check("case 1 proposed", prop.case, 1)
line = s.apply(prop, prop.options[0])

t = s.current.term
ok("term is an application", isinstance(t, LamApp))
check("[app(ta,td)] = D", type_of(t), "d")
check("[td] = D", type_of(t.arg), "d")
check("[ta] in this app = C", type_in_context(t, (0,)), "c")
check("[ta] alone is still A", type_of(t.func), "a")
check("user MOVED to D", s.current.here().iri, "d")
check("|P| unchanged", len(s.current.pointers), 1)
ok("calculus line mentions the move", "moved" in line)
check("step recorded", s.current.steps[0].answer, "to D")

# ── case 2 ────────────────────────────────────────────────────────────────
print("=== case 2: app(a,tb), user stays at B ===")

p2 = Proposal(case=2, point="the bit", question="reached how?",
              options=[Option("from A", entity=A), Option("from C", entity=C)])
s = sess([("the bit", B)], [p2])
s.open(B)
prop = s.propose()
s.apply(prop, prop.options[0])

t = s.current.term
check("[app(a,tb)] = B", type_of(t), "b")
check("[a] in this app = A", type_in_context(t, (0,)), "a")
check("operand is the FUNCTION", type_of(t.func), "a")
check("stayed-at term is the ARG", type_of(t.arg), "b")
check("user STAYED at B", s.current.here().iri, "b")

# ── case 3 ────────────────────────────────────────────────────────────────
print("=== case 3: reflection, app(tc,tc) shared ===")

p3 = Proposal(case=3, point="the bit", question="as a pair?",
              options=[Option("A/B", entity_a=A, entity_b=B),
                       Option("A/D", entity_a=A, entity_b=D)])
s = sess([("the bit", C)], [p3])
s.open(C)
prop = s.propose()
s.apply(prop, prop.options[0])

t = s.current.term
ok("term is a fan", isinstance(t, LamFan))
check("|P| grew by one", len(s.current.pointers), 2)
ok("ONE shared subject", subterm_at(t, (0,)) is subterm_at(t, (1,)))
check("both occurrences type as C (left)", type_of(subterm_at(t, (0,))), "c")
check("both occurrences type as C (right)", type_of(subterm_at(t, (1,))), "c")
check("question cast", t.grey_cast, "a")
check("answer cast", t.black_cast, "b")
pids = [p.pid for p in s.current.pointers.pointers]
check("both stand at C",
      [s.current.pointer_entities[p].iri for p in pids], ["c", "c"])

# ── option cap ────────────────────────────────────────────────────────────
print("=== at most 4 options; fewer than 2 is no choice ===")

many = Proposal(case=1, retype=C,
                options=[Option(f"o{i}", entity=D) for i in range(7)])
check("trimmed to 4", len(many.trimmed().options), 4)

one = Proposal(case=1, retype=C, options=[Option("only", entity=D)])
s = sess([("x", A)], [one])
s.open(A)
check("a single option is not a choice", s.propose().case, 0)

# ── case 4: skip ──────────────────────────────────────────────────────────
print("=== case 4: skip appends the question type ===")

s = sess([("x", A)], [])
s.open(A)
s.skip(Proposal(case=1, retype=C, options=[]))
check("skipped case 1 binds C", [e.iri for e in s.lambda_list], ["c"])
check("pointer stays in P", len(s.current.pointers), 1)
check("but is not offered", s.current.pointers.open_pointers(), [])

s = sess([("x", A)], [])
s.open(A)
s.skip(Proposal(case=3, options=[]))
check("skipped REFLECTION binds nothing", s.lambda_list, [])

# skipping the same entity twice binds once
s = sess([("x", A)], [])
s.open(A)
s.skip(Proposal(case=1, retype=C, options=[]))
s.current.pointers.select(0)
s.skip(Proposal(case=1, retype=C, options=[]))
check("one entity binds once", [e.iri for e in s.lambda_list], ["c"])

# ── case 5: resume ────────────────────────────────────────────────────────
print("=== case 5: resume binds the rest ===")

s = sess([("x", A), ("y", B), ("z", D)], [])
s.open(A)
left = s.resume()
check("open point bound", [e.iri for e in s.lambda_list][:1], ["a"])
ok("never-started subqueries bound too",
   {"b", "d"} <= {e.iri for e in s.lambda_list})
check("interaction closed by resume", s.current, None)
ok("session is done", s.done())

# A REFLECTION'S OCCURRENCES BIND NOTHING on resume either — case 3 forks
# rather than leaving a question, so resuming after one must not report its
# occurrences as "left open" when the user settled that point.
s = sess([("x", C)], [p3])
s.open(C)
prop = s.propose()
s.apply(prop, prop.options[0])
s.resume()
check("resume after a reflection binds nothing", s.lambda_list, [])

# and skipping a reflected pointer with no proposal binds nothing
s = sess([("x", C)], [p3])
s.open(C)
prop = s.propose()
s.apply(prop, prop.options[0])
s.skip()
check("skipping a reflected pointer binds nothing", s.lambda_list, [])

# ── closing and the next subquery ─────────────────────────────────────────
print("=== closing: (t,P) -> t, then the next subquery ===")

s = sess([("x", A), ("y", B)], [])
s.open(A)
c = s.close()
ok("closed has no pointer set", not hasattr(c, "pointers"))
check("[t] is the rightmost leaf", c.type_iri(), "a")
check("next subquery is offered", s.next_subquery().iri, "b")
s.open(B)
s.close()
ok("done when none left", s.done())

# combined term keeps [t] the rightmost leaf
check("combined [t]", type_of(s.combined_term()), "b")

# a closed interaction is offered as an answer
p = Proposal(case=1, retype=C, options=[Option("x", entity=D), Option("y", entity=E)])
s = sess([("x", A), ("y", B)], [p])
s.open(A)
s.close()
s.open(B)
s.propose()
ok("closed names reach the source", s.source.closed_seen[-1] == ["i1"])

# grafting a closed interaction as the answer
s = sess([("x", A), ("y", B)], [])
s.open(A)
s.close("done1")
s.open(B)
prop = Proposal(case=1, retype=C,
                options=[Option("that one", closed_name="done1"),
                         Option("D", entity=D)])
s.apply(prop, prop.options[0])
check("grafted closed interaction", type_of(s.current.term), "a")
check("[ta] = C at the graft", type_in_context(s.current.term, (0,)), "c")

# ── the interaction question ──────────────────────────────────────────────
print("=== interaction question: lam E1...lam En.(t) ===")

s = sess([("x", A)], [])
s.open(A)
s.skip(Proposal(case=1, retype=C, options=[]))
s.close()
q = s.interaction_question()
ok("outermost binder is a lambda", isinstance(q, LamAbs))
check("binds C", q.var.iri, "c")

# order: E1 outermost
s = sess([("x", A)], [])
s.open(A)
s._bind(C)
s._bind(D)
s.close()
q = s.interaction_question()
check("E1 outermost", q.var.iri, "c")
# Neither binder occurs in the body, so each is contracted in before being
# bound: λC.((λD.(A·D))·C). Walk past those applications to the next binder.
inner = q.body
while isinstance(inner, LamApp):
    inner = inner.func
check("E2 next", inner.var.iri if isinstance(inner, LamAbs) else None, "d")
# A binder must bind: the variable it introduces is the SAME node as the
# occurrence in its body, or it would bind nothing.
from interaction_engine import _occurs
ok("E1 binds an occurrence", _occurs(q.body, "c"))
ok("E2 binds an occurrence", _occurs(inner.body, "d"))

# no binders -> the term itself
s = sess([("x", A)], [])
s.open(A)
s.close()
q = s.interaction_question()
ok("no lambdaList means no binders", not isinstance(q, LamAbs))

# a binder that does not occur is contracted in first
s = sess([("x", A)], [])
s.open(A)
s._bind(E)
s.close()
q = s.interaction_question()
ok("absent binder is contracted in", isinstance(q.body, LamApp))
check("...and then bound", q.var.iri, "e")

# ── step budget ───────────────────────────────────────────────────────────
print("=== the loop is bounded ===")

p = Proposal(case=1, retype=C, options=[Option("x", entity=D), Option("y", entity=E)])
s = sess([("x", A)], [p, p, p], max_steps=2)
s.open(A)
s.apply(s.propose(), p.options[0])
s.apply(s.propose(), p.options[0])
check("budget stops the loop", s.propose().case, 0)

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
