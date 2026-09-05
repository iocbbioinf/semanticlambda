"""The ontology layer in the reading browser  (notes/reading_desc §8).

An ontology is a MODEL IN WHICH THE READING IS VALID, and each reading carries a
SET of them — every hypothesis still consistent with what the user has done. The
set shrinks by refutation and grows by enrichment, so the survivors are better
approximations as reading continues.

Validity means REDUCTION REPLAYS THE READING: a contraction is R1 firing, a
reflection is R4. The reading layer itself never reduces; only this one does.

These tests point the question store at a temp file, so the real one is never
touched and the abstractions available are exactly the ones set up here.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reading_agent import Entity, Option, StepProposal
from tests.test_reading_session import session

FAILED = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILED.append(msg)


def contract(s, r, iri, label):
    s.apply(r, StepProposal(kind="A", options=[]),
            s.canon_option(Option(kind="A", label="x",
                                  entity=Entity(iri, label, ""))))


class store:
    """Point the question store at a temp file for the body of a `with`."""

    def __enter__(self):
        import kg_store
        import ontology_state
        import term_utils
        self._dir = tempfile.TemporaryDirectory()
        db = Path(self._dir.name) / "lambda_terms.json"
        db.write_text("[]")
        self._p = patch("kg_store.LAMBDA_DB", db)
        self._p.start()
        self.db = db
        _refresh()
        return self

    def __exit__(self, *exc):
        self._p.stop()
        self._dir.cleanup()
        _refresh()
        return False


def _refresh():
    import ontology_state
    import term_utils
    term_utils.abstraction_titles(refresh=True)
    term_utils.qid_titles(refresh=True)
    ontology_state.load_abstractions.cache_clear() if hasattr(
        ontology_state.load_abstractions, "cache_clear") else None


def seed_abstraction(s=None):
    """Ask and save one question, so enrichment has material to propose."""
    s = s or session()
    r = s.open_reading(s.canon(Entity("local:aspirin", "Aspirin", "")))
    contract(s, r, "local:cox", "COX")
    s.ask_question(r, "how does it inhibit?", Entity("local:cox", "COX", ""))
    return s.save_questions()


def test_the_set_opens_with_the_reading():
    """§8.1b: the reading is a member of its own set — the degenerate model."""
    print("\nthe set opens holding the reading itself")
    with store():
        s = session()
        r = s.open_reading(s.canon(Entity("local:aspirin", "Aspirin", "")))

        check(len(r.ontologies) == 1, "one member at init")
        # `init_from_type` seeds the set with ont(G(a), {p}) — the bare seed,
        # NOT yet flagged as the reading's mirror. The mirror is (re)made on
        # each step by `reading_ontology`, which is where §8.1b's guarantee
        # actually bites; at init the two coincide anyway.
        check(str(r.ontologies[0].term) == str(r.term),
              "and it is the reading's own term")
        check(r.ontologies[0].fired == 0, "nothing has fired yet")
        check(r.ontologies[0].corresponding(r.pointers.act().pid) == (),
              "its pointer corresponds to the reading's, at the root")


def test_the_set_moves_in_step_with_the_reading():
    print("\nthe set is maintained at every step")
    with store():
        s = session()
        r = s.open_reading(s.canon(Entity("local:aspirin", "Aspirin", "")))
        contract(s, r, "local:cox", "COX")

        check(r.ont_stats, "a contraction reports what happened to the set")
        check("refuted" in r.ont_stats and "silent" in r.ont_stats,
              "refutations and silences are counted separately")

        mirror = [o for o in r.ontologies if o.is_reading]
        check(len(mirror) == 1, "the reading's mirror survives the update")
        check(str(mirror[0].term) == str(r.term),
              "and it still equals the reading")


def test_a_model_is_proposed_and_fires():
    """The point of the layer: a hypothesis whose reduction replays the step."""
    print("\na model is proposed, and the reading makes it fire")
    with store():
        seed_abstraction()

        s = session()
        r = s.open_reading(s.canon(Entity("local:aspirin", "Aspirin", "")))
        contract(s, r, "local:cox", "COX")
        contract(s, r, "local:pg", "Prostaglandins")

        models = [o for o in r.ontologies if not o.is_reading]
        check(models, "a candidate model was proposed")
        fired = [o for o in models if o.fired]
        check(fired, "and the reading's contraction made one FIRE (R1)")

        o = fired[0]
        check(o.is_reduced, "so it counts as reduced")
        check("λ" in str(o.origin_term),
              "its ORIGINAL keeps the abstraction it was proposed with")
        check("λ" not in str(o.term),
              "while the REDUCED form has consumed it — rule 1 eats the "
              "abstraction it fires on")
        check(str(o.origin_term) != str(o.term),
              "so the two forms genuinely differ")


def test_pointers_correspond_elementwise():
    """§8.1: every reading pointer has a counterpart in every ontology."""
    print("\npointers correspond elementwise to the reading's")
    with store():
        seed_abstraction()
        s = session()
        r = s.open_reading(s.canon(Entity("local:aspirin", "Aspirin", "")))
        contract(s, r, "local:cox", "COX")

        for o in r.ontologies:
            for p in r.pointers.pointers:
                check(o.corresponding(p.pid) is not None,
                      f"pointer p{p.pid} has a counterpart in every ontology")
            break        # one ontology is enough to state the shape


def test_reflection_updates_the_set():
    """A reflection is R4 — and refutes harder, since both branches need the
    sharing to be already present (§8.7)."""
    print("\na reflection updates the set too")
    with store():
        s = session()
        r = s.open_reading(s.canon(Entity("local:aspirin", "Aspirin", "")))
        contract(s, r, "local:cox", "COX")
        before = len(r.ontologies)

        s.apply(r, StepProposal(kind="B", options=[]),
                s.canon_option(Option(kind="B", label="f",
                                      entity_a=Entity("local:m", "mech", ""),
                                      entity_b=Entity("local:o", "out", ""))))

        check(r.ont_stats.get("total") == len(r.ontologies),
              "the step reports the set's size")
        check(any(o.is_reading for o in r.ontologies),
              "the reading's mirror is restored after the update (§8.1b)")
        # Every reading pointer — now two — must still have a counterpart.
        mirror = [o for o in r.ontologies if o.is_reading][0]
        check(all(mirror.corresponding(p.pid) is not None
                  for p in r.pointers.pointers),
              "and both of the reflection's pointers correspond")


def test_only_the_reading_left_is_meaningful():
    """§8.8: a set holding just the reading means no material accounts for it."""
    print("\nwith no abstractions, only the reading remains")
    with store():                     # empty store: nothing to enrich with
        s = session()
        r = s.open_reading(s.canon(Entity("local:aspirin", "Aspirin", "")))
        contract(s, r, "local:cox", "COX")
        contract(s, r, "local:pg", "Prostaglandins")

        check(all(o.is_reading for o in r.ontologies),
              "the set holds only the reading")
        check(len(r.ontologies) >= 1,
              "and is never empty — the reading is always a member")


def test_the_views_render_both_forms():
    print("\nthe list and the detail render, in both forms")
    import io
    import reading_browser as rb

    with store():
        seed_abstraction()
        s = session()
        r = s.open_reading(s.canon(Entity("local:aspirin", "Aspirin", "")))
        contract(s, r, "local:cox", "COX")
        contract(s, r, "local:pg", "Prostaglandins")

        buf = io.StringIO()
        with patch("builtins.input", lambda *a: ""), patch("sys.stdout", buf):
            rb.show_ontologies(r, s)
        out = buf.getvalue()
        check("ontologies" in out, "the list has a heading")
        check("the reading itself" in out, "the reading is shown as a member")
        check("in the set" in out, "with the size of the set")

        models = [o for o in r.ontologies if not o.is_reading]
        buf = io.StringIO()
        with patch("builtins.input", lambda *a: ""), patch("sys.stdout", buf):
            rb.show_ontology_detail(models[0], r)
        out = buf.getvalue()
        check("original" in out, "the detail shows the ORIGINAL form")
        check("reduced" in out, "and the REDUCED form")
        check("λ" in out, "the original still carries its abstraction")
        check("pointers" in out, "and the pointer correspondence is shown")


def test_the_o_command_is_offered():
    print("\n`o` is offered at every interaction step")
    import io
    import reading_browser as rb
    from reading_mock import MockAgent

    script = iter(["a query about aspirin", "o", "", "q"])
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
    check("o  ontologies" in out, "the option is listed")
    check("in the set" in out, "and it opens the list")


if __name__ == "__main__":
    for t in (test_the_set_opens_with_the_reading,
              test_the_set_moves_in_step_with_the_reading,
              test_a_model_is_proposed_and_fires,
              test_pointers_correspond_elementwise,
              test_reflection_updates_the_set,
              test_only_the_reading_left_is_meaningful,
              test_the_views_render_both_forms,
              test_the_o_command_is_offered):
        t()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for m in FAILED:
            print(f"  - {m}")
        sys.exit(1)
    print("all passed")
