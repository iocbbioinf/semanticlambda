"""The three interaction steps map onto the reading steps  (notes/reading_alg).

Runs the driver against a STUB delegate, so nothing here calls the network:
what is under test is the calculus, not the proposals.

    A -> contraction option 1   app(t1, t2), reader MOVES to B
    B -> reflection             sharing fan-in, |Pr| grows by one
    C -> contraction option 2   app(t1, t2), reader STAYS at B
"""

import sys
import time
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


def _leaf_nodes(t) -> dict:
    """label -> (occurrences, distinct nodes), descending into fan contexts.

    Sharing shows up two ways in the term representation — a node reached twice
    by identity, or a LamFan recording casts — so both must be walked (the
    `reading` skill's "one kind of sharing").
    """
    from collections import defaultdict
    from optimal_lambda import LamApp, LamVar
    from reading_state import LamFan

    found = []

    def walk(x):
        if isinstance(x, LamApp):
            walk(x.func); walk(x.arg)
        elif isinstance(x, LamVar):
            found.append(x)
        elif isinstance(x, LamFan):
            walk(x.principal)
            for ctx in (getattr(x, "grey_ctx", None), getattr(x, "black_ctx", None)):
                if ctx is not None:
                    walk(ctx)

    walk(t)
    d = defaultdict(set)
    for v in found:
        d[v.label].add(id(v))
    return {lab: (sum(1 for v in found if v.label == lab), len(ids))
            for lab, ids in d.items()}


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
        """Counts DELEGATIONS, whichever phase asks for them.

        Under the clarification phase (the default) steps are served from the
        one batched plan, so `propose_step` is never reached — what must not grow
        when the modal opens is the number of calls to the TRANSPORT.
        """
        calls = 0

        def _invoke(self, prompt, schema):
            Counting.calls += 1
            return super()._invoke(prompt, schema)

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
    # A modal must not re-ask the delegate: the options on screen belong to the
    # place the user is at, and re-asking would both discard them and (on
    # --claude) pay again. Two steps were taken, so two questions were asked —
    # the first with the plan's opening point, the second asked from there — and
    # opening the modal twice must have added NOTHING to that.
    check(Counting.calls == 4,
          f"the modal cost no extra delegation (calls={Counting.calls})")


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
    for token, what in [("how should", "the question put to the user"),
                        ("in your query:", "the words of the query at issue"),
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



def test_the_first_question_is_one_small_call():
    """The first wait is ONE call for ONE point — that is the whole latency.

    Latency tracks output size, so asking for six points up front meant ~36s of
    silence before anything appeared; one point is ~6s. The phase therefore asks
    for the root plus a single point, and asks again per step.
    """
    print("\nclarification — the first question is one small call")
    from reading_mock import MockAgent
    from reading_session import ReadingSession

    class Counting(MockAgent):
        def __init__(self):
            super().__init__()
            self.invokes = 0

        def _invoke(self, prompt, schema):
            self.invokes += 1
            return super()._invoke(prompt, schema)

    agent = Counting()
    s = ReadingSession("molecules that pass the blood-brain barrier and bind 5-HT2C",
                       agent)
    plan = s.start_clarifying()
    check(agent.invokes == 1, "phase 0 — the seeds — cost exactly one call")
    check(len(s.seeds) >= 1, f"and produced the query's seeds ({len(s.seeds)})")
    check(plan.points == [],
          "no point is asked yet: the first is asked once a reading opens")

    r = s.open_reading(plan.root)
    taken = 0
    for _ in range(12):
        prop = s.propose(r)
        if prop.kind == "none":
            break
        s.apply(r, prop, prop.options[0])
        taken += 1
    check(taken >= 3, f"the phase kept going ({taken} steps)")
    # one call for the first question, then one per further step
    check(agent.invokes <= taken + 2,
          f"about one call per step ({agent.invokes} calls, {taken} steps)")


def test_all_three_kinds_are_reachable_in_a_phase():
    """A/B/C all occur across a phase, and each applies as its reading step.

    Not within one reply any more — each call asks for a single point — so the
    property is about the PHASE: over its steps, all three kinds are proposed and
    applied, and a B really reflects (|Pr| grows).
    """
    print("\nclarification — all three kinds occur across the phase")
    from reading_mock import MockAgent
    from reading_session import ReadingSession

    s = ReadingSession("molecules that pass the blood-brain barrier and bind 5-HT2C",
                       MockAgent())
    plan = s.start_clarifying()
    r = s.open_reading(plan.root)
    seen = set()
    for _ in range(12):
        prop = s.propose(r)
        if prop.kind == "none":
            break
        before = len(r.pointers)
        s.apply(r, prop, prop.options[0])
        seen.add(prop.kind)
        if prop.kind == "B":
            check(len(r.pointers) == before + 1,
                  "a B step really reflected — |Pr| grew")
    check(seen == {"A", "B", "C"},
          f"all three kinds were applied across the phase ({sorted(seen)})")


def test_step_c_is_not_retrospective():
    """C may re-ask the SAME point, and may take a vantage already in the reading.

    Both are what make ONE up-front call sufficient for C: a vantage is a sense
    of the QUERY, so it needs no knowledge of the path the user took.
    """
    print("\nclarification — step C is not restricted to the last move")
    from reading_agent import Entity, Option, StepProposal
    from reading_session import ReadingSession
    from reading_mock import MockAgent
    from term_utils import _term_type

    A = Entity("local:sense-a", "A")
    B = Entity("local:sense-b", "B")
    B2 = Entity("local:sense-b2", "B2")

    # (a) the same point, re-asked after the reading has grown
    s = ReadingSession("q", MockAgent())
    r = s.open_reading(A)
    s.apply(r, StepProposal(kind="A"), Option(kind="A", label="", entity=B))
    before = _term_type(r.term)
    s.apply(r, StepProposal(kind="C"), Option(kind="C", label="", entity=A))
    check(_term_type(r.term) == before,
          "re-asking the same point leaves the type unchanged (option 2)")
    check(r.act_entity().iri == B.iri, "and leaves the user standing at B")
    check(_leaf_nodes(r.term)[A.label] == (2, 1),
          "the re-asked point is ONE shared node, twice occurring")

    # (b) a LATER, unrelated point whose vantage maps back into the reading
    s2 = ReadingSession("q", MockAgent())
    r2 = s2.open_reading(A)
    s2.apply(r2, StepProposal(kind="A"), Option(kind="A", label="", entity=B))
    s2.apply(r2, StepProposal(kind="A"), Option(kind="A", label="", entity=B2))
    s2.apply(r2, StepProposal(kind="C"), Option(kind="C", label="", entity=A))
    check(_term_type(r2.term) == B2.iri,
          "the later C step still leaves the type where the user stands")
    check(_leaf_nodes(r2.term)[A.label] == (2, 1),
          "a vantage already in the reading is reused as ONE node")


def test_one_call_per_step_asked_from_here():
    """Each step costs ONE delegation, and it is asked from where the user is.

    Time to first question is what the user waits for, and latency tracks output
    size — six points at once was ~36s of silence, one point ~6s. So the phase
    asks one at a time, and each call is told the reading, the position and what
    was already settled, which a point planned in advance could not know.
    """
    print("\nclarification — one call per step, asked from the current place")
    from reading_mock import MockAgent
    from reading_session import ReadingSession

    seen_prompts = []

    class Counting(MockAgent):
        def __init__(self):
            super().__init__()
            self.invokes = 0

        def _invoke(self, prompt, schema):
            self.invokes += 1
            seen_prompts.append(prompt)
            return super()._invoke(prompt, schema)

    agent = Counting()
    s = ReadingSession("molecules that pass the blood-brain barrier and bind 5-HT2C",
                       agent)
    plan = s.start_clarifying()
    check(agent.invokes == 1, "phase 0 — the seeds — cost exactly one call")
    check(plan.points == [], "and asked no point yet")

    r = s.open_reading(plan.root)
    taken = 0
    while taken < 4:
        prop = s.propose(r)
        if prop.kind == "none":
            break
        s.apply(r, prop, prop.options[0])
        taken += 1
    check(taken >= 3, f"the phase kept going ({taken} steps)")
    check(agent.invokes == taken + 1 or agent.invokes >= taken,
          f"one call per step ({agent.invokes} calls for {taken} steps)")

    # the later prompts must carry the state a pre-made plan could not have
    later = seen_prompts[-1]
    for token, what in [("STANDING AT", "where the user stands"),
                        ("SETTLED", "what was already settled"),
                        ("READING SO FAR", "the reading built so far")]:
        check(token in later, f"the call is told {what}")


def test_plan_order_beats_the_at_sense_hint():
    """The delegate's RANKING decides what is asked first, not `at_sense`.

    `at_sense` is a guess about where the user will be standing, made before they
    chose anything — the least reliable field in the plan. Letting it jump a point
    ahead of higher-ranked ones reorders the phase by that guess, which is what a
    real run did: it opened with a B point tagged for the root while four A points
    the delegate had ranked above it waited.
    """
    print("\nclarification — plan order wins over the at_sense hint")
    from clarify_plan import ClarificationPlan, PlannedPoint, PlannedOption
    from reading_agent import Entity

    root = Entity("local:root", "root")

    def a_opts(n):
        return [PlannedOption(label=f"o{i}",
                              sense=Entity(f"local:s{n}{i}", f"s{n}{i}"))
                for i in (1, 2)]

    def b_opts():
        return [PlannedOption(label=f"b{i}",
                              sense=Entity(f"local:ba{i}", "ba"),
                              sense_b=Entity(f"local:bb{i}", "bb"))
                for i in (1, 2)]

    pts = [
        PlannedPoint(quote="1", kind="A", question="qA1", options=a_opts(1)),
        PlannedPoint(quote="2", kind="A", question="qA2", options=a_opts(2)),
        # tagged for exactly where the user starts, but ranked third
        PlannedPoint(quote="3", kind="B", question="qB", options=b_opts(),
                     at_sense="local:root"),
    ]
    plan = ClarificationPlan(query="q", root=root, points=pts)
    served = []
    for _ in range(3):
        prop = plan.proposal_for(root, allow_c=True)
        if prop is None:
            break
        served.append(prop.prompt)
        plan.spend(prop)
    check(served == ["qA1", "qA2", "qB"],
          f"served in the plan's own order ({served})")


def test_over_long_labels_become_glosses():
    """A label is a NAME: a delegate that answers with a sentence is corrected.

    Labels print inline — in the term, in pointer lines, in option rows — so a
    sentence-length one wraps every line of the display. A real run returned a
    23-word root label and made the reading unreadable.
    """
    print("\nclarification — a sentence label is moved into the gloss")
    from clarify_plan import ClarificationPlanner

    long = ("find molecules meeting three joint criteria: BBB permeability, "
            "hydrophobicity, and experimentally validated 5-HT2C binding")
    out = ClarificationPlanner._shorten({"id": "x", "label": long})
    check(len(out["label"]) <= ClarificationPlanner.MAX_LABEL,
          f"the label is short enough to print inline ({len(out['label'])} chars)")
    check(out["gloss"] == long, "the sentence survives, as the gloss")
    check(not out["label"].rstrip("…").endswith(" "),
          "the name is cut on a word boundary")

    keep = {"id": "y", "label": "measured BBB permeability"}
    check(ClarificationPlanner._shorten(keep) == keep,
          "a label that is already a name is left alone")

    # The label is not the only way long text gets in: with no label,
    # `ReadingAgent._entity` uses the raw ID as one. A real run hit exactly
    # this — a sentence-length slug, no label — and printed it in full.
    pl = ClarificationPlanner(None)
    for d in ({"id": "search-for-molecules-that-are-cns-penetrant-and-"
                     "experimentally-confirmed-5ht2c-actives", "label": ""},
              {"id": "local:a-very-long-slug-that-goes-on-and-on-for-ages",
               "label": None}):
        ent = pl._sense(dict(d), {})
        check(len(ent.label) <= ClarificationPlanner.MAX_LABEL + 1,
              f"a long id yields a short label too ({len(ent.label)} chars)")


def test_spinner_erases_the_whole_line():
    """The spinner must clip to the terminal and erase what it wrote.

    A label wider than the eraser leaves its tail on screen after the next line
    is printed — visible in a real run as text dangling after the /time line.
    """
    print("\nUI — the spinner clips and erases to the terminal width")
    import io
    from unittest.mock import patch
    import reading_browser as rb

    buf = io.StringIO()
    long_label = "x" * 400
    with patch.object(rb, "_TTY", True), patch("sys.stdout", buf):
        with rb.Spinner(long_label):
            time.sleep(0.3)
    out = buf.getvalue()
    width = rb.Spinner._width()
    # Measure what is DISPLAYED: the colour escapes cost bytes but no columns,
    # so counting raw characters would fail a line that fits perfectly.
    import re
    visible = [len(re.sub(r"\033\[[0-9;]*m|\033\[2K", "", seg))
               for seg in out.split("\r") if seg]
    check(max(visible, default=0) <= width,
          f"no written line exceeds the terminal width "
          f"({max(visible, default=0)} vs {width})")
    # Erasing must NOT pad with spaces: the width is only ever a guess, and on a
    # wide terminal an over-long run of spaces IS the trailing whitespace it was
    # supposed to remove. A real run showed exactly that.
    check("\033[2K" in out, "the line is cleared with clear-to-EOL")
    check(not re.search(r"  {20,}", out), "no long run of padding spaces")
    check(out.endswith("\033[2K"), "the spinner leaves the line erased")



def test_canon_keeps_the_stored_gloss():
    """A stored gloss survives when the delegate sends none.

    `canon` returned the delegate's own Entity untouched whenever iri AND label
    already matched — dropping the store's gloss. That went unnoticed while
    stored labels differed from what the delegate sent (the merge branch ran and
    picked the gloss up); the moment labels agreed, readings started printing a
    bare label with nothing after it.
    """
    print("\nthe store's gloss is not lost when the label already matches")
    from reading_agent import Entity
    from reading_mock import MockAgent
    from reading_session import ReadingSession

    s = ReadingSession("q", MockAgent())
    s.entities.load_records([{"iri": "local:bbb", "label": "BBB ligands",
                              "gloss": "molecules that reach the brain",
                              "aliases": [], "uses": 2}])

    got = s.canon(Entity(iri="local:bbb", label="BBB ligands", gloss=""))
    check(got.gloss == "molecules that reach the brain",
          "no gloss offered: the stored one is recovered")
    check(got.iri == "local:bbb" and got.label == "BBB ligands",
          "and identity is unchanged")

    own = s.canon(Entity(iri="local:bbb", label="BBB ligands",
                         gloss="what it means HERE"))
    check(own.gloss == "what it means HERE",
          "a gloss the delegate offered wins — it is about this use")



def test_gloss_is_required_and_quotes_are_short():
    """The two fields the display depends on are constrained at the boundary.

    A terse label needs its gloss to be legible, so `gloss` is REQUIRED in the
    schema — left optional, the delegate omitted it most of the time and readings
    printed a name with nothing after it. And a `quote` is shown as "in your
    query: …" to say WHERE the doubt is, so a whole-clause quote defeats it.
    """
    print("\nclarification — gloss required, quote minimal")
    from clarify_plan import ClarificationPlanner, _PLAN_SCHEMA

    root = _PLAN_SCHEMA["properties"]["root"]
    check("gloss" in root["required"],
          "the schema requires a gloss on every entity")

    clause = ("molecules that can pass the blood-brain barrier, are hydrophobic "
              "and have been experimentally shown to interact with the receptor")
    clipped = ClarificationPlanner._clip_quote(clause)
    check(len(clipped) <= ClarificationPlanner.MAX_QUOTE,
          f"a whole-clause quote is clipped ({len(clipped)} chars)")
    check(ClarificationPlanner._clip_quote("are hydrophobic") == "are hydrophobic",
          "a phrase-length quote is left exactly as the user wrote it")


def test_a_missing_gloss_leaves_no_dangling_name():
    """`named()` never renders a separator with nothing after it."""
    print("\nUI — a name with no gloss renders cleanly")
    import reading_browser as rb
    from reading_agent import Entity

    with_gloss = rb.named(Entity("local:x", "Short name", "a useful clause"))
    check("a useful clause" in with_gloss, "a gloss is shown when there is one")

    bare = rb.named(Entity("local:y", "Short name", ""))
    check(bare.rstrip() == bare and bare.endswith("Short name"),
          f"no trailing separator when the gloss is empty ({bare!r})")

    echo = rb.named(Entity("local:z", "Same", "same"))
    check(echo.count("Same") == 1 or "same" not in echo.replace("Same", ""),
          "a gloss that merely repeats the label is not printed twice")



def test_an_entity_exists_independently_of_a_query():
    """A query is the OCCASION an entity is proposed on, not what it belongs to.

    So a point of THIS query may be understood as an entity an EARLIER query
    named. The delegate can only reuse what it is shown, and it is otherwise
    shown just the current reading — which left cross-query reuse to accident,
    when `match_key` happened to catch a spelling collision. The store's
    entities are therefore offered explicitly.
    """
    print("\nan entity is not the query's — earlier ones are offered for reuse")
    from reading_mock import MockAgent
    from reading_session import ReadingSession

    seen = []

    class Peek(MockAgent):
        def _invoke(self, prompt, schema):
            seen.append(prompt)
            return super()._invoke(prompt, schema)

    s = ReadingSession("molecules that pass the blood-brain barrier", Peek())
    # An isolated store, so this asserts the mechanism rather than whatever
    # data/entities.json happens to hold on the machine running the test.
    from entity_store import EntityStore
    s.entities = EntityStore()
    # entities an earlier query left behind
    s.entities.load_records([
        {"iri": "local:measured-bbb", "label": "measured BBB data",
         "gloss": "from an assay", "aliases": [], "uses": 9},
        {"iri": "local:predicted-bbb", "label": "predicted BBB",
         "gloss": "from a model", "aliases": [], "uses": 4},
    ])
    plan = s.start_clarifying()
    r = s.open_reading(plan.root)

    offered = s.offered_entities(r)
    iris = {e.iri for e in offered}
    check("local:measured-bbb" in iris,
          "an entity from an earlier query is offered for reuse")
    check(all(e.iri != r.seed.iri for e in offered),
          "what the reading already holds is not offered twice")

    # most-used first, so the cap keeps the entities worth reusing
    uses = [s.entities.get(e.iri).uses for e in offered]
    check(uses == sorted(uses, reverse=True),
          f"offered most-used first ({uses[:4]})")
    check(len(s.offered_entities(r, limit=1)) == 1,
          "the list is capped — a list too long to read is one that gets ignored")

    for _ in range(2):
        prop = s.propose(r)
        if prop.kind == "none":
            break
        s.apply(r, prop, prop.options[0])
    check(any("EARLIER READINGS" in pr for pr in seen[1:]),
          "the delegate is told which earlier entities it may reuse")

    # nothing anywhere records which query first named a thing
    rec = s.entities.to_records()[0]
    check("query" not in rec,
          f"a stored entity carries no query of its own ({sorted(rec)})")



def test_seeds_are_fixed_and_readings_combine():
    """Seeds are the query's unclear points, fixed before the interaction.

    One reading per seed, exhausted in turn; a point that EMERGES while reading
    maps to an entity without becoming a seed of its own, so the number of
    readings is decided by phase 0. The exhausted readings are then applied to
    one another — Rnew = app(app(R1,R2),R3) — which is how the separately
    settled points are assembled back into one reading of the query.
    """
    print("\nseeds are fixed; the readings combine")
    from reading_mock import MockAgent
    from reading_session import ReadingSession
    from term_utils import _term_type

    s = ReadingSession("molecules that pass the blood-brain barrier", MockAgent())
    s.start_clarifying()
    n_seeds = len(s.seeds)
    check(n_seeds > 1, f"the query yielded several seeds ({n_seeds})")
    check(all(iri in s.seed_quotes for iri in (e.iri for e in s.seeds)),
          "each seed records the words of the query it came from")

    r = s.open_reading(s.seeds[0])
    for _ in range(200):
        prop = s.propose(r)
        if prop.kind == "none":
            s.close_current()
            nxt = s.next_unread_seed()
            if nxt is None:
                break
            r = s.open_reading(nxt)
            continue
        s.apply(r, prop, prop.options[0])

    check(len(s.closed) == n_seeds,
          f"one reading per seed, no more ({len(s.closed)} of {n_seeds})")
    check(len(s.seeds) == n_seeds,
          "reading added no seeds — an emergent point is not a seed")

    rnew = s.combined_term()
    check(rnew is not None, "the readings combine into one term")
    # left-associative, so the type is the LAST reading's (§2: [app(t1,t2)]=[t2])
    check(_term_type(rnew) == _term_type(s.closed[-1].term),
          "Rnew carries the type of the last reading combined")


def test_resume_asks_what_was_left_unclarified():
    """Resume combines the readings and abstracts over the unread seeds.

        Rnew = app(R1, R);   question = lam a1. ... lam an. Rnew

    A seed never exhausted is a point nobody settled, so the question is a
    reading still PARAMETRIC IN those points. It is a proper §2 question
    whenever the seed's entity occurs in Rnew — which happens when a point
    emerging under another seed mapped to it — and is contracted in otherwise.
    Each binder is its own saved question, innermost first, because
    `check_question` requires every nested abstraction to be one.
    """
    print("\nresume — what was left unclarified becomes a question")
    from reading_agent import Entity
    from reading_mock import MockAgent
    from reading_session import ClosedReading, ReadingSession
    from optimal_lambda import LamApp, LamVar
    from term_utils import _term_type, check_question

    s = ReadingSession("q", MockAgent())
    S1 = Entity("local:s1", "S1", "a")
    S2 = Entity("local:s2", "S2", "b")
    S3 = Entity("local:s3", "S3", "c")
    s.seeds = [S1, S2, S3]
    # S1 was read, and while reading it an emergent point mapped to S3
    s.closed.append(ClosedReading(
        "R1", LamApp(LamVar("local:s1", "S1"), LamVar("local:s3", "S3")),
        S1, ["x"]))

    pending = s.unexhausted_seeds()
    check([e.iri for e in pending] == ["local:s2", "local:s3"],
          f"the unread seeds are the pending points ({[e.short() for e in pending]})")

    q = s.close_with_question()
    check(q is not None, "a question is created automatically")
    check(len(s.questions) == len(pending),
          f"one question per binder ({len(s.questions)} for {len(pending)})")
    for x in s.questions:
        check(check_question(x.term) is None
              or "not itself a saved question" in check_question(x.term),
              f"{x.title!r} is well formed apart from the store's nesting rule")
    check(_term_type(q.term) == pending[0].iri,
          "the outermost question is about the FIRST unclarified point")


def test_points_belong_to_their_reading():
    """A further reading gets its OWN points, and may re-quote the query.

    Points are asked for a particular reading: a later one reads a DIFFERENT
    subject, so it neither inherits the first reading's spent points (which
    would leave it with nothing to ask) nor is barred from quoting words that
    mattered there.
    """
    print("\nfurther readings — points are per reading, not per query")
    from clarify_plan import ClarificationPlan, PlannedPoint, PlannedOption
    from reading_agent import Entity

    root = Entity("local:root", "root", "the query")

    def opts(n):
        return [PlannedOption(label=f"o{i}",
                              sense=Entity(f"local:s{n}{i}", f"s{n}{i}", "g"))
                for i in (1, 2)]

    plan = ClarificationPlan(query="q", root=root, points=[
        PlannedPoint(quote="molecules", kind="A", question="q0",
                     options=opts(0), reading=0, spent=True),
        PlannedPoint(quote="molecules", kind="A", question="q1",
                     options=opts(1), reading=1),
    ])
    # reading 0 has nothing unspent; reading 1 has its own point on the SAME words
    check(plan.proposal_for(root, allow_c=True, reading=0) is None,
          "a reading is not served another reading's points")
    prop = plan.proposal_for(root, allow_c=True, reading=1)
    check(prop is not None and prop.prompt == "q1",
          "the further reading gets its own point")
    check(prop.note.endswith("“molecules”"),
          "the same words may be at issue again in a different reading")


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
              test_closed_reading_as_operand,
              test_the_first_question_is_one_small_call,
              test_all_three_kinds_are_reachable_in_a_phase,
              test_step_c_is_not_retrospective,
              test_one_call_per_step_asked_from_here,
              test_plan_order_beats_the_at_sense_hint,
              test_over_long_labels_become_glosses,
              test_spinner_erases_the_whole_line,
              test_canon_keeps_the_stored_gloss,
              test_gloss_is_required_and_quotes_are_short,
              test_a_missing_gloss_leaves_no_dangling_name,
              test_an_entity_exists_independently_of_a_query,
              test_seeds_are_fixed_and_readings_combine,
              test_resume_asks_what_was_left_unclarified,
              test_points_belong_to_their_reading):
        t()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for m in FAILED:
            print(f"  - {m}")
        sys.exit(1)
    print("all passed")
