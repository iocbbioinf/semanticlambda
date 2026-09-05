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
       contexts, and
       the only step that grows |Pr|.

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
from entity_store import EntityStore
from reading_agent import Entity, Option, ReadingAgent, StepProposal
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
        if st.iri == ent.iri and st.label == ent.label:
            return ent
        return Entity(iri=st.iri, label=st.label, gloss=st.gloss or ent.gloss)

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
        """Ask the delegate for the next step at actPtr."""
        here = reading.act_entity()
        if here is None:
            return StepProposal(kind="none", note="no entity at this pointer")
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
