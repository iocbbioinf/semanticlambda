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
    """A session with an EMPTY entity store.

    `ReadingSession` loads data/entities.json on start, so without this every
    test would depend on whatever the user's own store happens to hold — the
    tests must exercise the calculus, not one machine's reading history.
    """
    from unittest.mock import patch
    # `reading_session` imported the name, so patch it THERE.
    with patch("reading_session.load_entities", lambda *a, **k: 0):
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
    check(len(r.pointers) == 2, "|Pr| grew by one — the split")
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
            Option(kind="B", label="split", entity_a=E("a"), entity_b=E("b")))
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
            Option(kind="B", label="split", entity_a=E("x"), entity_b=E("y")))
    moved = s.mark_exhausted(r)
    check(moved is not None, "after a split there IS another pointer to move to")

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


def test_mock_agent():
    """The mock is the DEFAULT backend, so the driver must run fully on it."""
    print("\nmock delegate — the default backend")
    from reading_mock import MockAgent

    q = "how does aspirin reduce inflammation"
    check([e.iri for e in MockAgent().seed_entities(q)]
          == [e.iri for e in MockAgent().seed_entities(q)],
          "deterministic for one query, so a run can be repeated")
    check([e.iri for e in MockAgent().seed_entities(q)]
          != [e.iri for e in MockAgent().seed_entities("what is a fan-in")],
          "and differs between queries")
    check(MockAgent().total_cost_usd == 0.0, "costs nothing")

    m = MockAgent()
    ents = m.seed_entities(q)
    check(all(e.iri.startswith("local:") for e in ents),
          "entities are namespaced like the real delegate's")

    # It must reach all three kinds — that is the point of the fixed cycle.
    here = ents[0]
    kinds = []
    for _ in range(6):
        p = m.propose_step(query=q, term_text="t", here=here, history=[],
                           reached_from=None, known=ents, allow_c=True)
        if p.kind != "none":
            kinds.append(p.kind)
    check(set(kinds) >= {"A", "B", "C"},
          f"cycles through all three step kinds (saw {''.join(kinds)})")

    # Every option must carry what its kind needs, or the driver cannot apply it.
    m2 = MockAgent()
    for _ in range(6):
        p = m2.propose_step(query=q, term_text="t", here=here, history=[],
                            reached_from=None, known=ents, allow_c=True)
        if p.kind == "B":
            check(all(o.entity_a and o.entity_b for o in p.options),
                  "B options carry two entities — question and answer")
        elif p.kind in ("A", "C"):
            check(all(o.entity is not None for o in p.options),
                  f"{p.kind} options carry an operand entity")

    check(m2.propose_step(query=q, term_text="t", here=here, history=[],
                          reached_from=None, known=ents,
                          allow_c=False).kind != "C",
          "never proposes C where the driver forbids it")


def test_driver_runs_end_to_end_on_the_mock():
    """A whole session, no network: the mock drives real reading steps."""
    print("\nmock — a full session builds a real reading")
    from reading_mock import MockAgent
    from reading_session import ReadingSession

    s = ReadingSession("how does aspirin reduce inflammation", MockAgent())
    s.start()
    r = s.open_reading(s.next_unread_seed())
    for _ in range(6):
        prop = s.propose(r)
        if prop.kind == "none":
            break
        s.apply(r, prop, prop.options[0])

    check(len(r.steps) >= 3, f"took several steps ({len(r.steps)})")
    check(any(l.startswith("[A]") for l in r.steps), "a contraction option 1 fired")
    check(any(l.startswith("[B]") for l in r.steps), "a reflection fired")
    check(any(l.startswith("[C]") for l in r.steps), "a contraction option 2 fired")
    check(len(r.pointers) > 1, "the reflection grew |Pr|")


def test_select_pointer_switches_context():
    """After a reflection the user picks WHICH context the reading continues in.

    Reflection is the only step that grows Pr (§4.2), and its two pointers are
    independent positions. Without an explicit choice the user is stuck on
    whichever side `after_reflection` happened to select.
    """
    print("\nselecting actPtr — continuing in a chosen context")
    s = session()
    r = s.open_reading(E("t"))
    s.apply(r, StepProposal(kind="B", options=[]),
            Option(kind="B", label="split", entity_a=E("a"), entity_b=E("b")))
    left, right = r.pointers.pointers

    check(r.pointers.act().pid == left.pid,
          "reflection leaves the user in the left context by default")
    check(s.select_pointer(r, right.pid), "the other context can be selected")
    check(r.pointers.act().pid == right.pid, "actPtr moved to it")
    check(r.pointers.act().cast_type == "local:b",
          "and it carries that context's own cast")
    check(not s.select_pointer(r, 999), "selecting a pointer that isn't there fails")

    # Selecting un-exhausts: "nothing here" described a moment, not the position.
    r.exhausted.add(left.pid)
    s.select_pointer(r, left.pid)
    check(left.pid not in r.exhausted, "standing somewhere again un-exhausts it")


def test_contexts_grow_independently():
    """The point of choosing a context: the two sides grow apart over one subject."""
    print("\nthe two contexts are independent positions")
    s = session()
    r = s.open_reading(E("t"))
    s.apply(r, StepProposal(kind="B", options=[]),
            Option(kind="B", label="split", entity_a=E("a"), entity_b=E("b")))
    left, right = r.pointers.pointers

    s.select_pointer(r, left.pid)
    s.apply(r, StepProposal(kind="A", options=[]),
            Option(kind="A", label="x", entity=E("x")))
    s.select_pointer(r, right.pid)
    s.apply(r, StepProposal(kind="A", options=[]),
            Option(kind="A", label="y", entity=E("y")))

    fan = r.term
    check(str(fan.grey) != str(fan.black),
          "each context grew on its own — contracting at one leaves the other alone")
    check(fan.grey.arg.iri == "local:x" and fan.black.arg.iri == "local:y",
          "each grew the entity chosen in it")
    check(fan.grey.func is fan.black.func,
          "and the subject is still ONE node under both")
    check(len(r.pointers) == 2, "|Pr| unchanged by contractions")


def test_show_term_does_not_refetch_the_proposal():
    """`t` opens the modal and comes back to THE SAME options.

    Regression: it used to `continue` the outer loop, which asked the delegate
    for a fresh proposal — the options on screen were discarded, and on
    `--claude` they were paid for twice.
    """
    print("\n`t` — the modal holds, and does not re-ask the delegate")
    import io
    from unittest.mock import patch
    import reading_browser as rb
    from reading_mock import MockAgent

    class Counting(MockAgent):
        calls = 0

        def propose_step(self, **kw):
            Counting.calls += 1
            return super().propose_step(**kw)

    # one step, two looks at the reading, one more step, then resume.
    # The mock's second step is a reflection, which asks whether to continue in
    # the other context ("n" = stay), so that answer is in the script too.
    script = iter(["a query about aspirin", "1", "t", "", "t", "",
                   "1", "n", "r"])
    buf = io.StringIO()
    # `t` is a --verbose command: the modal shows the calculus.
    with patch("builtins.input", lambda *a: next(script)), \
         patch("reading_store.save_session", lambda s: "(not saved)"), \
             patch("reading_session.save_entities", lambda st, p=None: "(x)"), \
         patch.object(rb, "VERBOSE", True), \
         patch("sys.stdout", buf):
        rb.Browser(Counting()).run()
    out = buf.getvalue()

    check(out.count("the reading so far") == 2, "the modal opened both times")
    check(out.count("✓ [") == 2, "both chosen steps were applied")
    # one proposal per step taken, plus the one in flight when resume was hit;
    # a modal open must add NONE.
    check(Counting.calls == 3,
          f"the modal cost no extra proposal (calls={Counting.calls})")


def test_context_prompt_and_picker_in_the_ui():
    """The UI offers the other context after a reflection, and `p` at any time."""
    print("\nUI — choosing a context after the reflection, and with `p`")
    import io
    from unittest.mock import patch
    import reading_browser as rb
    from reading_mock import MockAgent

    def run(script):
        buf = io.StringIO()
        it = iter(script)
        with patch("builtins.input", lambda *a: next(it)), \
             patch("reading_store.save_session", lambda s: "(not saved)"), \
             patch("reading_session.save_entities", lambda st, p=None: "(x)"), \
             patch.object(rb, "VERBOSE", True), \
             patch("sys.stdout", buf):
            try:
                rb.Browser(MockAgent()).run()
            except StopIteration:
                pass          # the script ran out; whatever was printed stands
        return buf.getvalue()

    # step A, then the reflection, then ACCEPT the offer of the other context
    out = run(["a query about aspirin", "1", "1", "y", "q"])
    check("the reading split" in out, "the split is announced")
    # The offer itself goes through `input(prompt)`, which writes to the REAL
    # stdout rather than the patched one — so assert on what it DID, not on the
    # prompt text: accepting must land the user in the other context.
    check("now in context" in out and " at " in out,
          "accepting the offer switches to the other context")

    # decline, then reach the same choice through the `p` picker
    out = run(["a query about aspirin", "1", "1", "n", "p", "2", "q"])
    check("switch context" in out,
          "`p` is offered once the reading has split")
    check("where do you want to continue?" in out,
          "the picker lists the pointers")
    check("grey · left-up" in out and "black · right-up" in out,
          "each pointer says which aux port it is")
    check("now standing at" in out, "picking one moves actPtr there")

    # before any split there is nothing to choose, so `p` is not offered
    out = run(["a query about aspirin", "q"])
    check("switch context (" not in out,
          "`p` is hidden while there is only one pointer")


def test_verbose_gates_the_calculus_trace():
    """--verbose narrates the calculus; without it the app stays quiet.

    What is gated is only the NARRATION: the reading is built identically in
    both modes, so the test checks the same run twice and compares what is said.
    """
    print("\n--verbose — the calculus trace is opt-in")
    import io
    from unittest.mock import patch
    import reading_browser as rb
    from reading_mock import MockAgent

    def run(verbose):
        script = iter(["a query about aspirin", "1", "1", "n", "r"])
        buf = io.StringIO()
        with patch("builtins.input", lambda *a: next(script)), \
             patch("reading_store.save_session", lambda s: "(not saved)"), \
             patch("reading_session.save_entities", lambda st, p=None: "(x)"), \
             patch.object(rb, "VERBOSE", verbose), \
             patch("sys.stdout", buf):
            try:
                rb.Browser(MockAgent()).run()
            except StopIteration:
                pass
        return buf.getvalue()

    quiet, loud = run(False), run(True)

    # the trace the user asked to be gated
    for token, what in [("term  ", "the term"),
                        ("Pr  ", "the pointer set"),
                        ("reading step:", "which reading step"),
                        ("standing at:", "where the user stands"),
                        ("show the reading", "the `t` command"),
                        ("e  entities", "the `e` command"),
                        ("● step", "the kind-of-step line"),
                        ("contraction opt.", "the option number")]:
        check(token not in quiet, f"quiet: {what} is not shown")
        check(token in loud, f"verbose: {what} IS shown")

    # what must survive in BOTH: the user still has to see the choice
    for token, what in [("which do you take?", "the delegate's own question"),
                        ("saved", "the resume confirmation")]:
        check(token in quiet and token in loud, f"both: {what} is shown")

    # The pointer picker is a real choice in BOTH modes, but names the contexts by
    # their reading when quiet and by their machinery when verbose.
    def run_picker(verbose):
        script = iter(["a query about aspirin", "1", "1", "n", "p", "2", "q"])
        buf = io.StringIO()
        with patch("builtins.input", lambda *a: next(script)), \
             patch("reading_store.save_session", lambda s: "(not saved)"), \
             patch("reading_session.save_entities", lambda st, p=None: "(x)"), \
             patch.object(rb, "VERBOSE", verbose), \
             patch("sys.stdout", buf):
            try:
                rb.Browser(MockAgent()).run()
            except StopIteration:
                pass
        return buf.getvalue()

    quiet_p, loud_p = run_picker(False), run_picker(True)
    check("where do you want to continue?" in quiet_p,
          "quiet: the picker is still offered")
    check("context:" in quiet_p,
          "quiet: each place is named by the context it carries")
    for token, what in [("|Pr| =", "the pointer count"),
                        ("grey · left-up", "the aux port"),
                        ("now standing at", "the standing-at confirmation")]:
        check(token not in quiet_p, f"quiet: {what} is not shown")
        check(token in loud_p, f"verbose: {what} IS shown")


def test_render_reading_shows_sharing_once():
    """The tree draws a shared subject once and marks where it recurs."""
    print("\nthe modal's tree — sharing drawn once, not twice")
    import reading_browser as rb

    s = session()
    r = s.open_reading(E("t"))
    s.apply(r, StepProposal(kind="B", options=[]),
            Option(kind="B", label="split", entity_a=E("a"), entity_b=E("b")))
    lines = []
    rb.render_reading(r, lines)
    body = "\n".join(lines)

    check(any("fan-in" in l for l in lines), "the fan-in is drawn")
    check("↺" in body, "the shared subject is marked, not drawn as two subjects")
    # both pointers must be visible, or the view misrepresents Pr
    check("p1" in body and "p2" in body, "both pointers are marked in the tree")

    # §7.2: after option 2 at each port the two branches are DIFFERENT terms —
    # app(a,t) and app(b,t) — sharing only t. Reporting the whole branch as
    # shared would be wrong.
    #
    # Regression: LamFan.branch() REBUILDS its term per call, so walking
    # branch(0) then branch(1) let the first be collected and its id() reused by
    # the second — which then rendered as "shared with" the first. Sharing
    # reported where there is none.
    left, right = r.pointers.pointers
    r.pointers.select(left.pid)
    s.apply(r, StepProposal(kind="C", options=[]),
            Option(kind="C", label="a", entity=E("a")))
    r.pointers.select(right.pid)
    s.apply(r, StepProposal(kind="C", options=[]),
            Option(kind="C", label="b", entity=E("b")))

    lines2 = []
    rb.render_reading(r, lines2)
    body2 = "\n".join(lines2)
    check(sum("↺ shared" in l for l in lines2) == 0,
          "neither branch is reported as wholly shared with the other")
    check("a" in body2 and "b" in body2, "both branches are drawn in full")
    # BOTH occurrences are marked: the fan registers the subject before either
    # branch is walked, so neither copy is privileged as "the original" — which
    # is right, since the two occurrences are equals over one subject (§4.2).
    check(sum("↺ same node" in l for l in lines2) == 2,
          "both occurrences of t are marked as the one node")


if __name__ == "__main__":
    for t in (test_kind_a_is_contraction_option_1,
              test_entity_id_normalisation,
              test_mock_agent,
              test_select_pointer_switches_context,
              test_contexts_grow_independently,
              test_driver_runs_end_to_end_on_the_mock,
              test_show_term_does_not_refetch_the_proposal,
              test_context_prompt_and_picker_in_the_ui,
              test_verbose_gates_the_calculus_trace,
              test_render_reading_shows_sharing_once,
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
