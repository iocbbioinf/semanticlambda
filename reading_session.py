"""The driver — one query, many readings  (notes/reading_alg §8).

This is the calculus half of the browser app. It owns R = (G(t), Pr) for the
reading being built, applies the three interaction steps, and decides when a
reading is exhausted and a new one must be opened from another entity.

THE THREE INTERACTION STEPS ARE THE READING STEPS.

    A  an entity understood several ways   -> CONTRACTION, option 1
       actPtr at t1 [A], operand t2 [B]; app(t1, t2); the reader MOVES to B.
       An option may also be an ALREADY CREATED closed reading (reading_alg
       §3.1: a closed reading may be what the user answers WITH).

    B  a relation understood several ways  -> REFLECTION
       G(t) |-> a sharing fan-in over t; actPtr is replaced by TWO pointers on
       the aux-port edges, cast to A and B. This SPLITS the reading into two
       contexts, and is the only step that grows |Pr|.

    C  how this place was reached          -> CONTRACTION, option 2
       actPtr at t2 [B], operand t1 [A]; app(t1, t2); the reader STAYS at B and
       the operand becomes context behind them.

No reduction happens here: the reading layer never reduces (reading_alg §10). We
only build terms, bottom-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from optimal_lambda import LamApp, LamTerm
from entity_store import EntityStore, load_entities, save_entities
from reading_agent import Entity, Option, ReadingAgent, StepProposal
from clarify_plan import ClarificationPlan, ClarificationPlanner
from reading_state import (EntityRegistry, LamFan, PointerSet, replace_at,
                           subterm_at)
from term_utils import _term_type


@dataclass
class ClosedReading:
    """A saved reading — the pointer set is what closing drops (§1)."""
    name: str
    term: LamTerm
    seed: Entity
    steps: list[str] = field(default_factory=list)


@dataclass
class Question:
    """A question the user asked — an abstraction, reading_desc §2.

    `term` is `lam a.t`: the reading built so far as the body, the asked entity
    as the binder. `qid` is the SUBTYPE it names, minted from (asked, term,
    title) so that a different title is a different question even over the same
    body (§2).
    """
    title: str
    term: LamTerm
    asked: Entity
    qid: str
    query: str = ""


@dataclass
class Reading:
    """An OPEN reading: a term plus a pointer set, built from one seed entity."""
    seed: Entity
    term: LamTerm
    pointers: PointerSet
    entities: EntityRegistry
    # pid -> the entity that pointer stands at. Navigation uses the entity the
    # pointer stands at, NOT a reflection's cast (reading_state module docstring).
    pointer_entities: dict[int, Entity] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    used: list[Entity] = field(default_factory=list)
    # pids whose place the agent has declared exhausted, so the driver stops
    # re-asking there.
    exhausted: set[int] = field(default_factory=set)
    # ont(G(t), P) — the models in which this reading is valid (§8). Maintained
    # in step with every reading step; the reading itself is a member (§8.1b).
    ontologies: list = field(default_factory=list)
    ont_stats: dict = field(default_factory=dict)

    # ── access ────────────────────────────────────────────────────────────

    def act_pointer(self):
        return self.pointers.act()

    def act_subterm(self) -> LamTerm:
        p = self.pointers.act()
        return subterm_at(self.term, p.path) if p else self.term

    def act_entity(self) -> Optional[Entity]:
        p = self.pointers.act()
        return self.pointer_entities.get(p.pid) if p else None

    def replace_act(self, new: LamTerm) -> None:
        p = self.pointers.act()
        self.term = replace_at(self.term, p.path if p else (), new)

    def note_entity(self, ent: Entity) -> None:
        if all(e.iri != ent.iri for e in self.used):
            self.used.append(ent)

    def open_pointers(self) -> list:
        """Pointers that may still carry an interaction step."""
        return [p for p in self.pointers.pointers if p.pid not in self.exhausted]


class ReadingSession:
    """One user query; the readings built under it.

    `resume` means: save the initial question and all its readings.
    """

    def __init__(self, query: str, agent: ReadingAgent) -> None:
        self.query = query
        self.agent = agent
        self.seeds: list[Entity] = []
        self.seed_cursor = 0
        self.current: Optional[Reading] = None
        self.closed: list[ClosedReading] = []
        self.log: list[str] = []
        # Identity lives here, not in the delegate's answers. The delegate names
        # things loosely — "COX enzymes", then "the COX enzymes" — and each new
        # name would otherwise become a separate variable, losing the sharing
        # that makes a reused entity ONE node (§1).
        self.entities = EntityStore()
        # Entities persist across sessions: an entity named once should not be
        # re-invented under a fresh iri the next time, or the sharing that makes
        # a reused entity ONE node is lost between runs as well as within one.
        self.entities_loaded = load_entities(self.entities)
        self.questions: list[Question] = []
        # The clarification phase (clarify_plan): one batched call enumerates the
        # query's ambiguities, and every interaction step is then served from it
        # locally. None means the older per-step delegation is in force.
        self.plan: Optional[ClarificationPlan] = None
        self.planner: Optional[ClarificationPlanner] = None
        self.topups = 0
        self._want_points = 6
        # ONE SESSION PER QUERY: a ReadingSession IS one query, so its delegate
        # starts from an empty CONTEXT here and keeps that one session for the
        # whole interaction — the choices made while clarifying THIS query are
        # what the delegate should still hold; another query's are not.
        #
        # That is about context, not about identity. ENTITIES REMAIN GLOBAL: the
        # store below is loaded from data/entities.json across all queries, so an
        # entity named once is not re-invented under a fresh iri next time, and a
        # reused entity stays ONE node (§1) between runs as well as within one.
        if hasattr(agent, "new_session"):
            agent.new_session()

    # ── the entity store ──────────────────────────────────────────────────

    def canon(self, ent: Optional[Entity]) -> Optional[Entity]:
        """The canonical entity for a proposed one — merging near-duplicates.

        Everything the delegate proposes passes through here, so an entity that
        the store already holds comes back with the SAME iri, and therefore ends
        up as the same node of the reading's graph.
        """
        if ent is None:
            return None
        st = self.entities.resolve(ent.iri, ent.label, ent.gloss)
        # The store's gloss is worth keeping even when iri and label already
        # agree: a delegate that names an entity it has met before often sends no
        # gloss at all, and returning `ent` untouched would drop the description
        # the store holds — the reading then prints a bare label with nothing
        # after it. Take the delegate's gloss when it offered one (it is about
        # THIS use), the store's otherwise.
        gloss = (ent.gloss or "").strip() or st.gloss
        if st.iri == ent.iri and st.label == ent.label and gloss == ent.gloss:
            return ent
        return Entity(iri=st.iri, label=st.label, gloss=gloss)

    def canon_option(self, opt: Option) -> Option:
        """An option with each of its entities resolved against the store."""
        return Option(
            kind=opt.kind, label=opt.label, rationale=opt.rationale,
            entity=self.canon(opt.entity),
            entity_a=self.canon(opt.entity_a),
            entity_b=self.canon(opt.entity_b),
            reading_name=opt.reading_name,
        )

    # ── opening ───────────────────────────────────────────────────────────

    def start_clarifying(self, points: int = 6) -> ClarificationPlan:
        """THE FIRST PHASE — settle what was asked (clarify_plan).

        The query is mapped to one entity, which becomes the reading's seed: this
        is §3's "open on a type A", with the type being what the query is about.
        Every interaction step afterwards resolves one ambiguity IN THE QUERY.

        TIME TO FIRST QUESTION IS THE THING THE USER WAITS FOR, so this asks for
        the ROOT AND ONE POINT only (~6s), then fetches the remaining points on a
        BACKGROUND THREAD while the user reads that first question. Asking for
        all of them up front was one call but ~36s of silence — cheaper in total,
        and much worse to use.
        """
        self.planner = ClarificationPlanner(self.agent)
        self.plan = self.planner.plan_first(self.query)
        self._want_points = points
        root = self.canon(self.plan.root)
        self.plan.root = root
        # The senses of the plan are entities like any other: they go through the
        # store so a sense named twice is ONE node (§1).
        for pt in self.plan.points:
            for o in pt.options:
                o.sense = self.canon(o.sense)
                if o.sense_b is not None:
                    o.sense_b = self.canon(o.sense_b)
        self.seeds = [root]
        self.seed_cursor = 0
        return self.plan

    def start(self) -> list[Entity]:
        raw = self.agent.seed_entities(self.query)
        # Seeds go through the store too: two seeds naming one thing must not
        # start two readings of it.
        seen, seeds = set(), []
        for e in raw:
            c = self.canon(e)
            if c.iri not in seen:
                seen.add(c.iri)
                seeds.append(c)
        self.seeds = seeds
        return self.seeds

    def open_reading(self, seed: Entity) -> Reading:
        """INIT_FROM_TYPE (reading_alg §2): one entity, one pointer at the root."""
        reg = EntityRegistry()
        var = reg.get(seed.iri, seed.label)
        r = Reading(seed=seed, term=var, pointers=PointerSet.initial(),
                    entities=reg)
        act = r.pointers.act()
        if act is not None:
            r.pointer_entities[act.pid] = seed
        r.note_entity(seed)
        self.current = r
        self.init_ontologies(r)
        return r

    def next_unread_seed(self) -> Optional[Entity]:
        """The next entity that has no reading yet.

        "if there is no interaction step from P, continue to create a new reading
        from another entity with interaction steps that has no reading yet."
        """
        done = {c.seed.iri for c in self.closed}
        if self.current is not None:
            done.add(self.current.seed.iri)
        while self.seed_cursor < len(self.seeds):
            cand = self.seeds[self.seed_cursor]
            self.seed_cursor += 1
            if cand.iri not in done:
                return cand
        return None

    # ── proposing ─────────────────────────────────────────────────────────

    def propose(self, reading: Reading) -> StepProposal:
        """The next step at actPtr — from the plan when clarifying, else asked."""
        here = reading.act_entity()
        if here is None:
            return StepProposal(kind="none", note="no entity at this pointer")
        if self.plan is not None:
            return self._propose_from_plan(reading, here)
        # Kind C asks how this place was REACHED, and option 2 answers it by
        # building app(operand, stayed) AROUND where the user stands — so a bare
        # variable is a fine target (reading_alg §4 applies option 2 to exactly
        # that). What makes the question empty is a reading in which NOTHING has
        # happened yet: at the untouched root there is no reaching to account for.
        allow_c = bool(reading.steps)
        prop = self.agent.propose_step(
            query=self.query,
            term_text=str(reading.term),
            here=here,
            history=reading.steps,
            reached_from=self._reached_from(reading),
            known=reading.used,
            allow_c=allow_c,
        )
        prop.options = [self.canon_option(o) for o in prop.options]
        # Offer the user's own finished work as an option-1 operand (§3.1).
        if prop.kind == "A" and self.closed:
            for c in self.closed[-2:]:
                prop.options.append(Option(
                    kind="A", label=f"reading «{c.name}»",
                    rationale="a reading you already finished, used as the answer",
                    entity=c.seed, reading_name=c.name))
        return prop

    def _propose_from_plan(self, reading: Reading,
                           here: Entity) -> StepProposal:
        """Serve one planned point, topping up only when the plan runs dry.

        `allow_c` is False only where there is nothing to have been reached from
        — the untouched root. A planned C point is otherwise always servable: its
        vantage is a sense of the QUERY, so it needs no knowledge of the path the
        user took, and option 2 leaves the type unchanged whatever has been built
        (verified: `A · B` with operand A gives `A · (A · B)`, A one shared node).
        """
        assert self.plan is not None
        allow_c = bool(reading.steps)
        prop = self.plan.proposal_for(here, allow_c)

        # ONE STEP AT A TIME. The plan holds only what has been asked so far, so
        # when it has nothing left we ask for the next point FROM HERE — with the
        # reading, the position and what the user already settled in the prompt.
        # A point planned before those choices could only be ranked worse, and
        # the wait is the same either way (~6s: latency is output size, and one
        # point is one point).
        if prop is None and self.planner is not None:
            n = self.planner.plan_next(
                self.plan, term_text=str(reading.term), here=here,
                known=reading.used or [reading.seed], allow_c=allow_c)
            if n:
                for pt in self.plan.points[-n:]:
                    for o in pt.options:
                        o.sense = self.canon(o.sense)
                        if o.sense_b is not None:
                            o.sense_b = self.canon(o.sense_b)
                prop = self.plan.proposal_for(here, allow_c)

        if prop is None:
            return StepProposal(kind="none",
                                note="nothing left to clarify in the query")
        self.plan.spend(prop)
        prop.options = [self.canon_option(o) for o in prop.options]
        # A finished reading may be what the user answers WITH (§3.1) — the same
        # offer the per-step path makes.
        if prop.kind == "A" and self.closed:
            for c in self.closed[-2:]:
                prop.options.append(Option(
                    kind="A", label=f"reading «{c.name}»",
                    rationale="a reading you already finished, used as the answer",
                    entity=c.seed, reading_name=c.name))
        return prop

    @staticmethod
    def _reached_from(reading: Reading) -> Optional[str]:
        for line in reversed(reading.steps):
            if "→" in line:
                return line
        return None

    # ── the three steps ───────────────────────────────────────────────────

    def apply(self, reading: Reading, prop: StepProposal, opt: Option) -> str:
        if prop.kind == "A":
            return self._contract(reading, opt, option=1)
        if prop.kind == "C":
            return self._contract(reading, opt, option=2)
        if prop.kind == "B":
            return self._reflect(reading, opt)
        raise ValueError(f"not an applicable step kind: {prop.kind}")

    def _operand_term(self, reading: Reading, opt: Option) -> LamTerm:
        """OPERAND (reading_alg §3.1).

        A closed reading is grafted as a COPY of its term (SHARE_GRAFTED_READING
        defaults to false, O6). An entity goes through the registry, so an entity
        reused across steps is ONE node of the sharing graph (§1).
        """
        if opt.reading_name is not None:
            for c in self.closed:
                if c.name == opt.reading_name:
                    return _copy_term(c.term, reading.entities)
        ent = opt.entity
        return reading.entities.get(ent.iri, ent.label)

    def _contract(self, reading: Reading, opt: Option, option: int) -> str:
        """CONTRACT (reading_alg §3.1) — both options build app(t1, t2).

        option 1: actPtr at t1 [A], operand is t2 [B] — the reader MOVES to B.
        option 2: actPtr at t2 [B], operand is t1 [A] — the reader STAYS at B.
        """
        act = reading.pointers.act()
        stayed = reading.act_subterm()
        operand = self._operand_term(reading, opt)

        if option == 1:
            t1, t2 = stayed, operand
        else:
            t1, t2 = operand, stayed
        reading.replace_act(LamApp(t1, t2))

        a_iri, b_iri = _term_type(t1), _term_type(t2)
        if act is not None:
            reading.pointers.after_contraction(act, option)
            # Where the user stands afterwards: option 1 MOVED them to the
            # operand; option 2 leaves them where they already were.
            if option == 1 and opt.entity is not None:
                reading.pointer_entities[act.pid] = opt.entity
        if opt.entity is not None:
            reading.note_entity(opt.entity)
        if act is not None:
            self._ont_after_contraction(reading, act.pid, option, a_iri, b_iri,
                                        operand=operand, stayed=stayed)

        if option == 1:
            what = (f"reading «{opt.reading_name}»" if opt.reading_name
                    else opt.entity.short())
            line = (f"[A] contraction opt.1 — moved → {what}  "
                    f"[{_short(a_iri)} asks, {_short(b_iri)} answers]")
        else:
            line = (f"[C] contraction opt.2 — reached from "
                    f"{opt.entity.short()}, stayed  "
                    f"[{_short(a_iri)} asks, {_short(b_iri)} answers]")
        reading.steps.append(line)
        return line

    def _reflect(self, reading: Reading, opt: Option) -> str:
        """REFLECT (reading_alg §3.2) — the reading splits into two contexts.

        G(t) becomes a sharing fan-in over t: t occurs twice and exists once.
        actPtr is replaced by TWO pointers on the aux-port edges, cast to A and
        B. This is the only step that grows |Pr|, and the only one that breaks
        the typing (§4.2).
        """
        act = reading.pointers.act()
        here = reading.act_entity()
        t = reading.act_subterm()
        ea, eb = opt.entity_a, opt.entity_b

        reading.replace_act(LamFan(principal=t, grey_cast=ea.iri,
                                   black_cast=eb.iri))
        if act is not None:
            left, right = reading.pointers.after_reflection(act, ea.iri, eb.iri)
            # Navigation continues from C on BOTH branches: the two pointers
            # stand where the user already was; the casts ride on the pointers.
            reading.pointer_entities.pop(act.pid, None)
            reading.pointer_entities[left.pid] = here
            reading.pointer_entities[right.pid] = here
            reading.exhausted.discard(act.pid)
            self._ont_after_reflection(reading, act.pid, left.pid, right.pid,
                                       ea.iri, eb.iri, _term_type(t))
        reading.note_entity(ea)
        reading.note_entity(eb)

        line = (f"[B] reflection — {ea.short()} (question) ⋅ "
                f"{eb.short()} (answer) over {here.short() if here else '?'}  "
                f"[split: |Pr| = {len(reading.pointers)}]")
        reading.steps.append(line)
        return line

    # ── advancing ─────────────────────────────────────────────────────────

    @staticmethod
    def select_pointer(reading: Reading, pid: int) -> bool:
        """Make `pid` the actPtr — "the place where the user stays" (§1).

        Before each step the user SELECTS one pointer (reading_alg §8). Reflection
        is what makes that a real choice: it replaces actPtr with two pointers on
        the fan-in's aux ports, and the two contexts grow INDEPENDENTLY — contracting
        at one occurrence leaves the others alone. Without this the user is stuck
        with whichever context the reflection happened to leave selected.

        Selecting also un-exhausts the place: "nothing to propose here" was an
        answer about a moment, not a verdict on the position, and the user asking
        to stand there again is reason enough to ask once more.
        """
        if not reading.pointers.select(pid):
            return False
        reading.exhausted.discard(pid)
        return True

    def mark_exhausted(self, reading: Reading) -> Optional[int]:
        """No step here; move actPtr to another open pointer if there is one."""
        act = reading.pointers.act()
        if act is not None:
            reading.exhausted.add(act.pid)
        for p in reading.open_pointers():
            reading.pointers.select(p.pid)
            return p.pid
        return None

    # ── closing ───────────────────────────────────────────────────────────

    # ── the ontology set  (reading_desc §8) ───────────────────────────────

    def _ont_paths(self, reading: Reading) -> dict:
        """The reading's pointer paths by pid — its mirror's correspondence."""
        return {p.pid: p.path for p in reading.pointers.pointers}

    @staticmethod
    def _ont_type_at(term, path):
        """Type at a position — §2's typing, as the ontology rules need it."""
        return _term_type(subterm_at(term, path))

    def init_ontologies(self, reading: Reading) -> None:
        """Open the set for a fresh reading (§8.3)."""
        import ontology_state as ont
        act = reading.pointers.act()
        if act is None:
            return
        root = reading.entities.get(reading.seed.iri, reading.seed.label)
        reading.ontologies = ont.init_from_type(root, act.pid)

    def _ont_after_contraction(self, reading: Reading, pid: int, option: int,
                               a_iri, b_iri, operand=None, stayed=None,
                               operand_onts=None) -> None:
        """UPDATE then ENRICH, in step with the contraction (§8.6).

        THE MIRROR IS PREPARED FIRST, and the order is part of the rule:
          1 drop the old mirror; 2 add one equal to the reading AFTER this step;
          3 point it at tb — in BOTH options, since §4.1 makes tb the B side
            either way; 4 ENRICH that mirror, at that pointer; 5 run the ordinary
            update over the whole set.

        Step 3 before 4 is what makes the enrichment productive: §8.4 substitutes
        at a pointed VARIABLE, and tb is one where the application just built is
        not.
        """
        import ontology_state as ont

        candidates = ont.strip_reading_ontology(reading.ontologies)
        mirror = ont.reading_ontology(reading.term, self._ont_paths(reading))
        mirror = ont.point_reading_mirror_at_tb([mirror], pid)[0]
        candidates = candidates + ont.enrich([mirror])

        # A contraction also PROPOSES models: for each saved abstraction of the
        # type at the pointer, app(q, operand) — a rule-1 redex whose firing IS
        # this contraction.
        if operand is not None:
            ppath = self._ont_paths(reading).get(pid, ())
            if option == 2 and stayed is not None:
                candidates = candidates + ont.candidates_for_option2(
                    pid, ppath, a_iri, stayed)
            else:
                candidates = candidates + ont.candidates_for_contraction(
                    pid, ppath, a_iri, b_iri, operand)

        reading.ontologies, stats = ont.after_contraction(
            candidates, pid, option, a_iri, b_iri, self._ont_type_at,
            operand_onts)
        reading.ontologies = ont.enrich(reading.ontologies)
        reading.ontologies, dropped = ont.cap(reading.ontologies)
        # §8.1b: the set is DEFINED to hold the reading, so restore the mirror
        # if the update refuted it.
        if not any(o.is_reading for o in reading.ontologies):
            reading.ontologies = (ont.point_reading_mirror_at_tb([mirror], pid)
                                  + reading.ontologies)
        stats["dropped"] = dropped
        stats["total"] = len(reading.ontologies)
        reading.ont_stats = stats

    def _ont_after_reflection(self, reading: Reading, pid: int, left_pid: int,
                              right_pid: int, a_iri, b_iri, c_iri) -> None:
        """UPDATE then ENRICH, in step with the reflection (§8.7)."""
        import ontology_state as ont

        candidates = ont.strip_reading_ontology(reading.ontologies)
        reading.ontologies, stats = ont.after_reflection(
            candidates, pid, left_pid, right_pid, a_iri, b_iri, c_iri,
            self._ont_type_at)
        reading.ontologies = ont.enrich(reading.ontologies)
        reading.ontologies, dropped = ont.cap(reading.ontologies)
        reading.ontologies = ont.sync_reading_ontology(
            reading.ontologies, reading.term, self._ont_paths(reading))
        stats["dropped"] = dropped
        stats["total"] = len(reading.ontologies)
        reading.ont_stats = stats

    # ── asking a question ─────────────────────────────────────────────────

    def ask_question(self, reading: Reading, title: str,
                     asked: Entity) -> tuple[str, Reading]:
        """Ask `title` of `asked`: save the question, then read on from it.

        A QUESTION IS AN ABSTRACTION (reading_desc §2): `lam a.t`, where `t` is
        the reading built so far and `a` the entity being asked about. It names a
        SUBTYPE of that entity's type, and the questions of an entity form a tree
        under it — the entity itself being the most general question of its type.

        THE ASKED ENTITY MUST OCCUR IN THE BODY. `term_utils.check_question`
        refuses an abstraction whose bound variable does not occur free: the
        question would claim to be about an entity its graph never mentions, and
        since [G(lam a.t)] == [a] enrichment would then offer it as a candidate of
        that type although reducing it can never place the entity. So an entity
        that is not yet in the reading is CONTRACTED IN first — which is exactly
        how §2 describes building a question's body: read the body, contract the
        asked material in, then bind that occurrence.

        The new reading starts AT THE QUESTION: its seed carries the minted
        subtype iri, so the reader is standing in the question rather than back at
        the plain entity (§2, "how a subtype variable renders").
        """
        from optimal_lambda import LamAbs
        from question_tree import mint_qid

        # A QUESTION SUBTYPE IS ALREADY AN IDENTITY and must not go through the
        # entity store: `canon` would slugify its qid into a fresh `local:` id,
        # which destroys the subtype the question names and points the parent
        # link at an entity that does not exist. Only plain entities are merged.
        from question_tree import is_question_type
        if not is_question_type(asked.iri):
            asked = self.canon(asked)

        # (1) make sure the asked entity is IN the body — see the docstring.
        if not _occurs(reading.term, asked.iri):
            self._contract(reading, Option(kind="A", label=asked.label,
                                           entity=asked), option=1)

        var = reading.entities.get(asked.iri, asked.label)
        term = LamAbs(var=var, qid=None, body=reading.term)

        # (2) the subtype this question names. Derived from (parent, term,
        # title), so it is stable and a different TITLE is a different question
        # even over the same body and the same asked entity (§2).
        qid = mint_qid(str(term), asked.iri, title)
        self.questions.append(Question(title=title, term=term, asked=asked,
                                       qid=qid, query=self.query))

        # (3) close what was being read — the question's body is finished work —
        # and open a new reading standing IN the question.
        self.close_current()
        seed = Entity(iri=qid, label=title, gloss=f"a question asked of "
                                                 f"{asked.label}")
        return qid, self.open_reading(seed)

    def save_entities(self):
        """Write the entity store to data/entities.json."""
        return save_entities(self.entities)

    def save_questions(self) -> tuple[int, list[str]]:
        """Persist the session's questions to the shared question store.

        Goes through `term_utils.append_lambda_term`, which is the ONLY way
        material enters that store — enrichment draws its candidates from exactly
        there, so everything in it must be a question a user actually asked.

        The graph passed is EMPTY: these entities are not KG nodes, so there are
        no claims to collect. That is a real absence, not a lookup failure.
        """
        import rdflib
        from term_utils import append_lambda_term

        g = rdflib.Graph()
        saved, failed = 0, []
        for q in self.questions:
            try:
                append_lambda_term(q.title, q.term, g, parent=q.asked.iri,
                                   origin="reading-browser")
                saved += 1
            except ValueError as e:
                failed.append(str(e))
        if saved:
            # AFTER the writes, so what the picker reads next includes them.
            _invalidate_question_cache()
        return saved, failed

    def close_current(self, name: Optional[str] = None) -> Optional[ClosedReading]:
        """SAVE (reading_alg §3.3): total, and collapses the pointer set."""
        r = self.current
        if r is None:
            return None
        cr = ClosedReading(name=name or f"{r.seed.label} — {self.query[:32]}",
                           term=r.term, seed=r.seed, steps=list(r.steps))
        self.closed.append(cr)
        self.current = None
        return cr


def _invalidate_question_cache() -> None:
    """Drop the caches over the question store, so a write is visible at once.

    `term_utils` memoises its title lookups and the picker memoises the store's
    questions; both would otherwise keep showing what was there before the save.
    """
    try:
        from term_utils import abstraction_titles, qid_titles
        qid_titles(refresh=True)
        abstraction_titles(refresh=True)
    except Exception:
        pass
    try:
        import reading_browser
        reading_browser._STORED_Q = None
    except Exception:
        pass


def _occurs(t: LamTerm, iri: str) -> bool:
    """Does `iri` occur anywhere in `t`? — the body test of §2."""
    from optimal_lambda import LamAbs, LamVar

    if isinstance(t, LamVar):
        return t.iri == iri
    if isinstance(t, LamApp):
        return _occurs(t.func, iri) or _occurs(t.arg, iri)
    if isinstance(t, LamFan):
        return _occurs(t.branch(0), iri) or _occurs(t.branch(1), iri)
    if isinstance(t, LamAbs):
        return _occurs(t.body, iri)
    return False


def _copy_term(t: LamTerm, reg: EntityRegistry) -> LamTerm:
    """Deep-copy a grafted term, re-minting its variables through `reg`.

    An entity that the grafted reading and the host reading have in common must
    end up as ONE node, which is exactly what routing every variable through the
    host's registry achieves.
    """
    from optimal_lambda import LamVar

    if isinstance(t, LamVar):
        return reg.get(t.iri, t.label)
    if isinstance(t, LamApp):
        return LamApp(_copy_term(t.func, reg), _copy_term(t.arg, reg))
    if isinstance(t, LamFan):
        return LamFan(principal=_copy_term(t.principal, reg),
                      grey_ctx=t.grey_ctx, black_ctx=t.black_ctx,
                      grey_cast=t.grey_cast, black_cast=t.black_cast)
    return t


def _short(iri: Optional[str]) -> str:
    if not iri:
        return "?"
    return iri.split(":", 1)[-1].split("/")[-1].split("#")[-1]
