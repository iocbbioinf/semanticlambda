"""The three interaction steps map onto the reading steps  (notes/reading_alg).

Runs the driver against a STUB delegate, so nothing here calls the network:
what is under test is the calculus, not the proposals.

    A -> contraction option 1   app(t1, t2), reader MOVES to B
    B -> reflection             sharing fan-in, |Pr| grows by one
    C -> contraction option 2   app(t1, t2), reader STAYS at B
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optimal_lambda import LamApp, LamVar
from reading_agent import Entity, Option, StepProposal
from reading_session import ReadingSession
from reading_state import LamFan
from term_utils import _term_type

FAILED = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILED.append(msg)


class StubAgent:
    """Stands in for `claude -p`; the driver cannot tell the difference."""

    def __init__(self):
        self.total_cost_usd = 0.0

    def seed_entities(self, query, limit=6):
        return [E("aspirin"), E("cox"), E("inflammation")]

    def propose_step(self, **kw):
        return StepProposal(kind="none")


def E(slug, label=None):
    return Entity(iri=f"local:{slug}", label=label or slug)


def session():
    return ReadingSession("why does aspirin work", StubAgent())


# ── A: contraction, option 1 ──────────────────────────────────────────────────

def test_kind_a_is_contraction_option_1():
    print("\nA — entity understood several ways -> contraction option 1")
    s = session()
    r = s.open_reading(E("aspirin"))
    prop = StepProposal(kind="A", options=[])
    opt = Option(kind="A", label="as an inhibitor", entity=E("cox"))
    s.apply(r, prop, opt)

    check(isinstance(r.term, LamApp), "builds an application")
    check(isinstance(r.term.func, LamVar) and r.term.func.iri == "local:aspirin",
          "t1 is where the user stood [A]")
    check(isinstance(r.term.arg, LamVar) and r.term.arg.iri == "local:cox",
          "t2 is the operand [B]")
    # [app(a,b)] = [b]: the type after the step is B, and the reader is there.
    check(_term_type(r.term) == "local:cox", "type after the step is B")
    check(r.act_entity().iri == "local:cox", "the reader MOVED to B")
    check(len(r.pointers) == 1, "|Pr| unchanged")


# ── C: contraction, option 2 ──────────────────────────────────────────────────

def test_kind_c_is_contraction_option_2():
    print("\nC — how this place was reached -> contraction option 2")
    s = session()
    r = s.open_reading(E("inflammation"))
    before = r.act_entity().iri
    s.apply(r, StepProposal(kind="C", options=[]),
            Option(kind="C", label="via COX", entity=E("cox")))

    check(isinstance(r.term, LamApp), "builds an application")
    check(r.term.func.iri == "local:cox", "the OPERAND is t1 [A] — function side")
    check(r.term.arg.iri == "local:inflammation",
          "where the user stood is t2 [B] — argument side")
    check(_term_type(r.term) == "local:inflammation", "type after the step is B")
    check(r.act_entity().iri == before, "the reader STAYED at B")
    check(len(r.pointers) == 1, "|Pr| unchanged")


# ── B: reflection ─────────────────────────────────────────────────────────────

def test_kind_b_is_reflection():
    print("\nB — relation understood several ways -> reflection")
    s = session()
    r = s.open_reading(E("aspirin"))
    s.apply(r, StepProposal(kind="B", options=[]),
            Option(kind="B", label="two ways", entity_a=E("cox1"),
                   entity_b=E("cox2")))

    check(isinstance(r.term, LamFan), "builds a sharing fan-in")
    check(len(r.pointers) == 2, "|Pr| grew by one — the fork")
    ptrs = r.pointers.pointers
    check({p.cast_type for p in ptrs} == {"local:cox1", "local:cox2"},
          "A and B are cast onto the two occurrences")
    check(all(p.is_reflected() for p in ptrs), "both pointers carry a cast")
    check(ptrs[0].path == (0,) and ptrs[1].path == (1,),
          "pointers designate the aux-port edges, not the enclosing node")
    # "both branches ARE the shared t": one subject, held once.
    check(r.term.grey is r.term.black,
          "one subject, reached through both branches")
    check(r.term.principal.iri == "local:aspirin", "the subject is what was there")


# ── §7.2: argument-position sharing ───────────────────────────────────────────

def test_argument_position_sharing():
    """BUILD_ARG_SHARING (reading_alg §4): reflect, then option 2 at each port."""
    print("\n§7.2 — reflect, then option 2 at both ports")
    s = session()
    r = s.open_reading(E("t"))
    s.apply(r, StepProposal(kind="B", options=[]),
            Option(kind="B", label="fork", entity_a=E("a"), entity_b=E("b")))
    left, right = r.pointers.pointers

    r.pointers.select(left.pid)
    s.apply(r, StepProposal(kind="C", options=[]),
            Option(kind="C", label="a", entity=E("a")))
    r.pointers.select(right.pid)
    s.apply(r, StepProposal(kind="C", options=[]),
            Option(kind="C", label="b", entity=E("b")))

    fan = r.term
    check(isinstance(fan, LamFan), "still one fan over one subject")
    grey, black = fan.grey, fan.black
    check(isinstance(grey, LamApp) and grey.func.iri == "local:a",
          "grey occurrence became app(a, t)")
    check(isinstance(black, LamApp) and black.func.iri == "local:b",
          "black occurrence became app(b, t)")
    # The point of the construction: t is in ARGUMENT position of both, and is
    # still ONE node.
    check(grey.arg is black.arg, "t is SHARED — one node, two occurrences")
    check(grey.arg.iri == "local:t", "and it is the original subject")


# ── entity reuse is sharing ───────────────────────────────────────────────────

def test_entity_reuse_is_one_node():
    print("\nentity reuse — the same entity is ONE node")
    s = session()
    r = s.open_reading(E("aspirin"))
    s.apply(r, StepProposal(kind="A", options=[]),
            Option(kind="A", label="→cox", entity=E("cox")))
    s.apply(r, StepProposal(kind="A", options=[]),
            Option(kind="A", label="→aspirin again", entity=E("aspirin")))

    # app(app(aspirin, cox), aspirin) — the two aspirins are the same object.
    check(r.term.arg is r.term.func.func,
          "an entity used twice is the same node (EntityRegistry)")


# ── advancing between readings ────────────────────────────────────────────────

def test_exhausted_pointer_then_next_seed():
    print("\ndriver — exhausted place, then the next unread entity")
    s = session()
    s.seeds = [E("aspirin"), E("cox")]
    r = s.open_reading(s.seeds[0])
    check(s.mark_exhausted(r) is None, "single pointer exhausted -> nowhere to go")

    s.apply(r, StepProposal(kind="B", options=[]),
            Option(kind="B", label="fork", entity_a=E("x"), entity_b=E("y")))
    moved = s.mark_exhausted(r)
    check(moved is not None, "after a fork there IS another pointer to move to")

    s.close_current("r1")
    check(len(s.closed) == 1, "closing collapses the reading into the store")
    nxt = s.next_unread_seed()
    check(nxt is not None and nxt.iri == "local:cox",
          "the driver moves to an entity with no reading yet")


def test_closed_reading_as_operand():
    print("\na closed reading used as an option-1 operand")
    s = session()
    r0 = s.open_reading(E("cox"))
    s.apply(r0, StepProposal(kind="A", options=[]),
            Option(kind="A", label="→pg", entity=E("prostaglandin")))
    s.close_current("cox-reading")

    r = s.open_reading(E("aspirin"))
    s.apply(r, StepProposal(kind="A", options=[]),
            Option(kind="A", label="answer with it", entity=E("cox"),
                   reading_name="cox-reading"))
    check(isinstance(r.term, LamApp), "grafts as an application")
    check(isinstance(r.term.arg, LamApp),
          "the whole closed term lands in argument position")
    check(_term_type(r.term) == "local:prostaglandin",
          "type after the step is the closed reading's own type")


def test_entity_id_normalisation():
    """The delegate echoes ids back as `local:x`; that must not mint a new entity.

    Regression: slugifying `local:aspirin` naively gives `local-aspirin`, so the
    id becomes `local:local-aspirin` and the registry sees a DIFFERENT entity —
    silently losing the sharing that makes a reused entity one node.
    """
    print("\nentity ids — a re-proposed entity is the same entity")
    from reading_agent import ReadingAgent
    known = [Entity("local:aspirin", "Aspirin")]
    seen = {e.iri.split(":", 1)[-1]: e for e in known}

    check(ReadingAgent._entity({"id": "local:aspirin"}, seen) is known[0],
          "a namespaced id resolves to the SAME entity object")
    check(ReadingAgent._entity({"id": "Aspirin"}, seen) is known[0],
          "so does the bare, differently-cased form")
    fresh = ReadingAgent._entity({"id": "COX Enzymes", "label": "COX"}, seen)
    check(fresh.iri == "local:cox-enzymes", "a new id is slugified once")
    check(ReadingAgent._entity({"id": "local:cox-enzymes"}, seen) is fresh,
          "and is then reused, not re-minted")


if __name__ == "__main__":
    for t in (test_kind_a_is_contraction_option_1,
              test_entity_id_normalisation,
              test_kind_c_is_contraction_option_2,
              test_kind_b_is_reflection,
              test_argument_position_sharing,
              test_entity_reuse_is_one_node,
              test_exhausted_pointer_then_next_seed,
              test_closed_reading_as_operand):
        t()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for m in FAILED:
            print(f"  - {m}")
        sys.exit(1)
    print("all passed")
