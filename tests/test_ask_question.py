"""Asking a question — the abstraction layer  (notes/reading_desc §2, §8.2).

A question is `lam a.t`: the reading built so far as the BODY, the entity being
asked about as the BINDER. It names a SUBTYPE of that entity's type, so the
questions of an entity form a tree under it, and the entity is the most general
question of its type — the root.

Nothing here writes to the real question store: `save_questions` is exercised
against a temporary file.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optimal_lambda import LamAbs, LamApp, LamVar
from reading_agent import Entity, Option, StepProposal
from term_utils import check_question
from tests.test_reading_session import E, session

FAILED = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILED.append(msg)


def built(s, *entities):
    """A reading over `entities`, contracted left to right."""
    r = s.open_reading(s.canon(entities[0]))
    for e in entities[1:]:
        s.apply(r, StepProposal(kind="A", options=[]),
                s.canon_option(Option(kind="A", label="x", entity=e)))
    return r


def test_question_is_an_abstraction():
    print("\na question is lam a.t — the reading is the body")
    s = session()
    r = built(s, Entity("local:aspirin", "Aspirin", ""),
                 Entity("local:cox", "COX", ""))
    body_before = str(r.term)

    qid, new = s.ask_question(r, "how does it inhibit?",
                              Entity("local:cox", "COX", ""))
    q = s.questions[-1]

    check(isinstance(q.term, LamAbs), "an abstraction is built")
    check(q.term.var.iri == "local:cox", "the ASKED entity is the binder")
    check(str(q.term.body) == body_before,
          "the reading as it stood is the body")
    check(check_question(q.term) is None,
          "and it is well formed — the bound variable occurs free")


def test_asking_about_an_entity_not_in_the_reading():
    """§2: build the body by reading, contract the asked material IN, then bind.

    `check_question` refuses an abstraction whose bound variable does not occur
    free — the question would be "about nothing", and since [G(lam a.t)] == [a]
    enrichment would offer it as a candidate of a type it can never place.
    """
    print("\nasking about an entity that is not in the reading yet")
    s = session()
    r = built(s, Entity("local:aspirin", "Aspirin", ""))
    outside = Entity("local:pg", "Prostaglandins", "")

    qid, new = s.ask_question(r, "what does it produce?", outside)
    q = s.questions[-1]

    check(check_question(q.term) is None,
          "the question is well formed all the same")
    check("local:pg" in str(_iris(q.term.body)),
          "because the entity was contracted into the body first")
    check(q.term.var.iri == "local:pg", "and it is the binder")


def test_question_names_a_subtype():
    print("\na question names a SUBTYPE of the entity's type")
    from question_tree import is_question_type

    s = session()
    r = built(s, Entity("local:aspirin", "Aspirin", ""))
    qid, new = s.ask_question(r, "how does it work?",
                              Entity("local:aspirin", "Aspirin", ""))

    check(is_question_type(qid), "the minted iri is a question subtype")
    check(s.questions[-1].qid == qid, "the question records it")
    check(new.seed.iri == qid,
          "and the new reading stands IN the question, not at the entity")
    check(new.seed.label == "how does it work?",
          "the reading renders the question's TITLE")


def test_a_different_title_is_a_different_question():
    """§2: the title is part of what is asked, not a label on top of it."""
    print("\nsame body, same entity, different title -> different question")
    s = session()
    asked = Entity("local:aspirin", "Aspirin", "")

    r1 = built(s, asked)
    q1, _ = s.ask_question(r1, "how does it work?", asked)
    r2 = built(s, asked)
    q2, _ = s.ask_question(r2, "what is it for?", asked)

    check(q1 != q2, "the two subtypes differ")
    check(s.questions[0].term.body is not s.questions[1].term.body
          or str(s.questions[0].term) == str(s.questions[1].term),
          "even where the term is identical")


def test_asking_closes_the_reading_and_opens_one_in_the_question():
    print("\nasking closes the reading and starts a new one")
    s = session()
    r = built(s, Entity("local:aspirin", "Aspirin", ""),
                 Entity("local:cox", "COX", ""))
    check(len(s.closed) == 0, "nothing closed yet")

    qid, new = s.ask_question(r, "why?", Entity("local:cox", "COX", ""))

    check(len(s.closed) == 1, "the reading that became the body was closed")
    check(s.current is new, "and the new reading is the current one")
    check(len(new.pointers) == 1, "which opens with one pointer, at its root")
    check(str(new.term) == "why?", "standing in the question itself")


def test_save_questions_writes_to_the_store():
    """The store is the ONLY way material reaches enrichment, so it must land."""
    print("\nsaving goes through the shared question store")
    import term_utils

    s = session()
    r = built(s, Entity("local:aspirin", "Aspirin", ""),
                 Entity("local:cox", "COX", ""))
    s.ask_question(r, "how does it inhibit?", Entity("local:cox", "COX", ""))

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "lambda_terms.json"
        db.write_text("[]")
        with patch.object(term_utils, "LAMBDA_DB", db, create=True), \
             patch("kg_store.LAMBDA_DB", db):
            saved, failed = s.save_questions()
            check(saved == 1 and not failed, f"saved (failed={failed})")
            recs = json.loads(db.read_text())

        check(len(recs) == 1, "one record written")
        rec = recs[0]
        check(rec["chain_label"] == "how does it inhibit?", "the title is stored")
        check(rec["term_str"].startswith("λCOX."), "the abstraction is stored")
        check(rec["parent"] == "local:cox",
              "the parent is the type the binder has")
        check(rec.get("qid"), "and the subtype iri is recorded")
        check(rec.get("origin") == "reading-browser", "tagged with its origin")
        check(rec["claims"] == [],
              "no claims: these entities are not KG nodes, a real absence")


def test_the_ui_no_longer_offers_asking():
    """Questions are no longer the user's to ask — they are created on resume.

    `a` is gone from the step menu: a question is now built automatically from
    what the interaction left unclarified (`close_with_question`), so offering
    the user a second, manual way to make one would put material in the store
    that no reading accounts for.
    """
    print("\nthe step menu no longer offers `a`")
    import io
    import reading_browser as rb
    from reading_mock import MockAgent

    script = iter(["a query about aspirin", "1", "1", "n", "r"])
    buf = io.StringIO()
    with patch("builtins.input", lambda *a: next(script)), \
         patch("reading_store.save_session", lambda s: "(not saved)"), \
         patch("reading_session.save_entities", lambda st, p=None: "(x)"), \
         patch("sys.stdout", buf):
        try:
            rb.Browser(MockAgent()).run()
        except StopIteration:
            pass
    out = buf.getvalue()

    check("ask a question" not in out, "the `a` option is not listed")
    check("what is the question about?" not in out,
          "and its entity picker is never shown")
    # what replaces it: the question the resume built
    check("left unclarified" in out or "the query, read" in out,
          "resume reports the reading it assembled instead")


def test_stored_questions_are_selectable_as_the_binder():
    """A question already in the store may be what a new question is asked OF.

    A question names a SUBTYPE (§2), so asking a further question of one is what
    makes questions form a tree. The store holds every question ever asked — this
    app's and the KG browser's — so those must be offered too.
    """
    print("\nasking about a question already in the store")
    from question_tree import is_question_type

    # Ask one FIRST, so the store has a question whatever it shipped with.
    s = session()
    r0 = built(s, Entity("local:aspirin", "Aspirin", ""))
    qid, _ = s.ask_question(r0, "how does it work?",
                            Entity("local:aspirin", "Aspirin", ""))
    title = "how does it work?"

    r = built(s, Entity("local:cox", "COX", ""))
    new_qid, new = s.ask_question(r, "and what follows?",
                                  Entity(iri=qid, label=title, gloss=""))
    q = s.questions[-1]

    # THE BUG THIS GUARDS: routing a subtype through the entity store slugifies
    # its qid into a fresh `local:` id, destroying the identity the question
    # names and pointing the parent link at an entity that does not exist.
    check(q.term.var.iri == qid,
          "the binder keeps the stored question's own subtype iri")
    check(is_question_type(q.term.var.iri), "which is still a question type")
    check(q.asked.iri == qid, "so the parent link names the real question")
    check(check_question(q.term) is None, "and the abstraction is well formed")
    check(is_question_type(new_qid) and new_qid != qid,
          "the new question is a distinct subtype below it")


def test_picker_offers_stored_questions_when_filtered():
    """Unfiltered shows this session; typing also reaches the store."""
    print("\nthe picker reaches the store once you type")
    import reading_browser as rb
    import incremental_select
    from reading_mock import MockAgent
    from term_utils import qid_titles

    # An earlier test points the store at a temp file; both the picker's cache
    # and term_utils' own hold what was read then. Refresh both so this test
    # sees the real store.
    rb._STORED_Q = None
    qid_titles(refresh=True)

    s = session()
    r = built(s, Entity("local:aspirin", "Aspirin", ""))

    captured = {}

    def fake_select(prompt, search, render, **kw):
        captured["search"] = search
        captured["render"] = render
        return None

    real = incremental_select.select
    incremental_select.select = fake_select
    try:
        rb.pick_entity(r, s, "what is the question about?")
    finally:
        incremental_select.select = real

    search, render = captured["search"], captured["render"]
    unfiltered = [e.label for e in search("")]
    check("Aspirin" in unfiltered, "this session's entities are listed")
    check(len(unfiltered) <= 8,
          "and the list stays short when nothing is typed")

    # A word from a question's title must reach it. Ask one here rather than
    # assuming the store shipped with any — then rebuild the picker, since its
    # search closes over the pool as it stood when it was opened.
    r2 = built(s, Entity("local:cox", "COX", ""))
    s.ask_question(r2, "distinctiveword what follows?",
                   Entity("local:cox", "COX", ""))

    captured.clear()
    incremental_select.select = fake_select
    try:
        rb.pick_entity(s.current, s, "what is the question about?")
    finally:
        incremental_select.select = real
    search, render = captured["search"], captured["render"]

    hits = [e.label for e in search("distinctiveword")]
    check(any("distinctiveword" in h.lower() for h in hits),
          "typing a word from a question's title reaches it")
    check("?" in render(search("distinctiveword")[0], False),
          "and questions are marked as questions")


def test_entity_picker_filters_by_typing():
    """Typing narrows the list; a number chooses — as the KG browser's box did.

    Driven DIRECTLY: the picker no longer has a caller in the step loop, since
    `a` is gone and questions are built on resume. It is kept because choosing
    an entity from a filtered list is the shape any future picker needs, and
    `tests/test_incremental_select.py` covers the live type-ahead widget.
    """
    print("\nthe entity picker filters as you type")
    import io
    import reading_browser as rb
    from reading_mock import MockAgent
    from reading_session import ReadingSession

    s = ReadingSession("aspirin and inflammation", MockAgent())
    s.start_clarifying()
    r = s.open_reading(s.seeds[0])
    # give the reading a second entity, so filtering has something to choose
    prop = s.propose(r)
    if prop.kind != "none":
        s.apply(r, prop, prop.options[0])

    # Scripted input is not a tty, so the picker uses its plain fallback:
    # a filter line, then the number of the row wanted.
    script = iter(["", "1"])
    buf = io.StringIO()
    with patch("builtins.input", lambda *a: next(script)), \
         patch("sys.stdout", buf):
        try:
            picked = rb.pick_entity(r, s, "what is the question about?")
        except StopIteration:
            picked = None
    out = buf.getvalue()

    check("type to filter" in out, "the picker says how to use it")
    check("what is the question about?" in out, "it puts the prompt")
    check(picked is not None, "and an entity is chosen")


def _iris(t):
    out = []
    if isinstance(t, LamVar):
        out.append(t.iri)
    elif isinstance(t, LamApp):
        out += _iris(t.func) + _iris(t.arg)
    elif isinstance(t, LamAbs):
        out += _iris(t.body)
    return out


if __name__ == "__main__":
    for t in (test_question_is_an_abstraction,
              test_asking_about_an_entity_not_in_the_reading,
              test_question_names_a_subtype,
              test_a_different_title_is_a_different_question,
              test_asking_closes_the_reading_and_opens_one_in_the_question,
              test_save_questions_writes_to_the_store,
              test_the_ui_no_longer_offers_asking,
              test_entity_picker_filters_by_typing,
              test_stored_questions_are_selectable_as_the_binder,
              test_picker_offers_stored_questions_when_filtered):
        t()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for m in FAILED:
            print(f"  - {m}")
        sys.exit(1)
    print("all passed")
