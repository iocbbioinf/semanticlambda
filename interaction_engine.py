"""The interaction engine — the loop, headless.

One query becomes one `QuerySession`. The query is decomposed into SUBQUERIES,
each mapped to an entity; each subquery is read by its own `Interaction`, which
is a term plus a pointer set. When no unclear point is left, the interaction is
CLOSED — (t, P) -> t — and the next subquery is taken up. When no subquery is
left, the session builds the INTERACTION QUESTION and is done.

    decompose query -> subqueries, each mapped to an entity
    loop:
        point = the unclear point of the current subquery
        if point:  case 1 | skip(4) | resume(5)
        else:      close; take the next subquery; exit when none left

NO I/O HERE. Nothing in this module talks to a model, a database or a terminal.
It takes proposals in and hands state back, so the whole loop is unit-testable
without spending a token — which is also what lets the web layer be a thin shell
over it. The delegate that invents questions and options sits behind
`StepSource`; the mock in the tests satisfies it.

THE LAMBDALIST. The interaction question is `lam E1...lam En.(t)`, and the
binders are the points nobody settled:

    case 4  skipping a point appends its question type
    case 5  resuming appends every point still open
    -       a subquery never started appends its entity

Order is the order they were left in, and one entity binds once however often it
was left (a repeated binder would bind nothing the second time).

ONLY CASE 1 IS IN THE LOOP. Every unclear point is clarified by contraction
option 1 — the user chooses what the words refer to and moves there. Contraction
option 2 and reflection are not offered; a proposal of any other case is treated
as "nothing unclear here".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from optimal_lambda import LamAbs, LamVar
from interaction_state import (
    EntityRegistry, LamApp, Path, Pointer, PointerSet,
    replace_at, subterm_at, type_of,
)


# ── what the engine is given ──────────────────────────────────────────────


@dataclass(frozen=True)
class Entity:
    """A variable of the interaction. `iri` is a stable local id, not a KG IRI."""
    iri: str
    label: str
    gloss: str = ""

    def short(self) -> str:
        return self.label or self.iri


@dataclass(frozen=True)
class Option:
    """One offered way of clarifying the point.

    `entity` is the ANSWER (D); the user moves there. `closed_name` instead
    names an already-closed interaction, which the spec admits as an answer.
    """
    label: str
    rationale: str = ""
    entity: Optional[Entity] = None
    closed_name: Optional[str] = None


@dataclass
class Proposal:
    """What is offered at one point.

        case  1 | 0        0 means "no unclear point here"
        point the words of the subquery at issue
        question  the question put to the user
        options   up to 4; a choice needs at least 2
        retype    C — what the function-position term is typed as at the
                  application this step builds
    """
    case: int
    point: str = ""
    question: str = ""
    options: list[Option] = field(default_factory=list)
    retype: Optional[Entity] = None

    MAX_OPTIONS = 4

    def trimmed(self) -> "Proposal":
        """At most 4 options, per the spec."""
        if len(self.options) <= self.MAX_OPTIONS:
            return self
        return Proposal(case=self.case, point=self.point, question=self.question,
                        options=self.options[:self.MAX_OPTIONS],
                        retype=self.retype)


class StepSource(Protocol):
    """Whoever invents the points, questions and options."""

    def seeds(self, query: str) -> list[tuple[str, Entity]]:
        """Decompose the query: (words of the subquery, entity it maps to)."""

    def propose(self, query: str, subquery: str, term, here: Entity,
                closed: list[str]) -> Proposal:
        """The next unclear point of this subquery, or `Proposal(case=0)`."""


# ── the record ────────────────────────────────────────────────────────────


@dataclass
class Step:
    """One step as the user met it, kept beside the calculus line.

    The calculus says what the term did; it does not say what was asked or
    answered. Both are needed: one replays the interaction, the other reads it.
    """
    case: int
    point: str
    question: str
    answer: str
    calculus: str = ""
    rationale: str = ""

    def __str__(self) -> str:
        head = f"“{self.point}” — {self.question}" if self.point else self.question
        return f"{head}  →  {self.answer}"


@dataclass
class Interaction:
    """An OPEN interaction: a term plus a pointer set, read from one subquery."""
    seed: Entity
    subquery: str
    term: object
    pointers: PointerSet
    entities: EntityRegistry
    # pid -> the entity that pointer stands at.
    pointer_entities: dict[int, Entity] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)

    @property
    def act(self) -> Optional[Pointer]:
        return self.pointers.act()

    def here(self) -> Optional[Entity]:
        """The entity the actPtr stands at."""
        a = self.act
        return self.pointer_entities.get(a.pid) if a else None

    def subterm(self):
        a = self.act
        return subterm_at(self.term, a.path) if a else None


@dataclass
class ClosedInteraction:
    """A closed interaction — closing is what drops the pointer set."""
    name: str
    seed: Entity
    subquery: str
    term: object
    steps: list[Step] = field(default_factory=list)

    def type_iri(self) -> Optional[str]:
        """[t] — the rightmost leaf."""
        return type_of(self.term)


# ── the session ───────────────────────────────────────────────────────────


class QuerySession:
    """One user's query, from decomposition to the interaction question."""

    def __init__(self, query: str, source: StepSource,
                 user: str = "", max_steps: int = 40) -> None:
        self.query = query
        self.source = source
        self.user = user
        self.max_steps = max_steps

        self.seeds: list[Entity] = []
        self.seed_quotes: dict[str, str] = {}
        self.current: Optional[Interaction] = None
        self.closed: list[ClosedInteraction] = []
        self.started: set[str] = set()
        # The binders of the interaction question, in the order they were left.
        self.lambda_list: list[Entity] = []
        self._steps_taken = 0
        # One registry for the whole query, so an entity reused across
        # subqueries is ONE node.
        self.entities = EntityRegistry()

    # ── decomposition ─────────────────────────────────────────────────────

    def start(self) -> list[Entity]:
        """Decompose the query into subqueries, each mapped to an entity."""
        pairs = self.source.seeds(self.query)
        seen: set[str] = set()
        for quote, ent in pairs:
            if ent.iri in seen:
                continue
            seen.add(ent.iri)
            self.seeds.append(ent)
            self.seed_quotes[ent.iri] = quote
        return self.seeds

    def next_subquery(self) -> Optional[Entity]:
        """A subquery with no interaction yet."""
        for s in self.seeds:
            if s.iri not in self.started:
                return s
        return None

    def open(self, seed: Entity) -> Interaction:
        """Begin an interaction for `seed`, from scratch."""
        self.started.add(seed.iri)
        var = self.entities.get(seed.iri, seed.label)
        inter = Interaction(
            seed=seed,
            subquery=self.seed_quotes.get(seed.iri, ""),
            term=var,
            pointers=PointerSet.initial(),
            entities=self.entities,
        )
        inter.pointer_entities[inter.act.pid] = seed
        self.current = inter
        return inter

    # ── the loop ──────────────────────────────────────────────────────────

    def propose(self) -> Proposal:
        """The next unclear point of the current interaction."""
        inter = self.current
        if inter is None or inter.act is None:
            return Proposal(case=0)
        if self._steps_taken >= self.max_steps:
            return Proposal(case=0)
        here = inter.here()
        if here is None:
            return Proposal(case=0)
        p = self.source.propose(self.query, inter.subquery, inter.term, here,
                                [c.name for c in self.closed])
        p = p.trimmed()
        # Only case 1 is in the loop. A single option is not a choice: the
        # user is asked to pick between rival senses, so one offers nothing.
        if p.case != 1 or len(p.options) < 2:
            return Proposal(case=0)
        return p

    def apply(self, prop: Proposal, opt: Option) -> str:
        """Apply the chosen option. Returns the calculus line."""
        inter = self.current
        if inter is None or inter.act is None:
            raise ValueError("no open interaction")
        if prop.case != 1:
            raise ValueError(f"not an applicable case: {prop.case}")
        line = self._case1(inter, prop, opt)

        inter.steps.append(Step(case=prop.case, point=prop.point,
                                question=prop.question, answer=opt.label,
                                calculus=line, rationale=opt.rationale))
        self._steps_taken += 1
        return line

    @staticmethod
    def _register(inter: Interaction, ent: Optional[Entity]) -> Optional[str]:
        """Put `ent` in the registry and return its iri.

        A retype is an entity that may never appear as a term node — the term
        keeps only its iri — so registering it is what keeps its name findable
        afterwards.
        """
        if ent is None:
            return None
        inter.entities.get(ent.iri, ent.label)
        return ent.iri

    def _operand(self, inter: Interaction, opt: Option):
        """The term an option contributes.

        A closed interaction is grafted as a COPY, so that closing it stays
        final. An entity goes through the registry, so an entity reused across
        steps is ONE node.
        """
        if opt.closed_name is not None:
            for c in self.closed:
                if c.name == opt.closed_name:
                    return _copy(c.term, inter.entities)
        ent = opt.entity
        if ent is None:
            # Reached only if a source offered an option with nothing to graft.
            # `AgentSource` drops those at its boundary; saying so plainly here
            # beats an AttributeError from deep in the term builder.
            raise ValueError(
                f"option “{opt.label}” names no entity to contribute")
        return inter.entities.get(ent.iri, ent.label)

    def _case1(self, inter: Interaction, prop: Proposal, opt: Option) -> str:
        """Contraction option 1 — app(ta, td); the user MOVES to the answer.

            [app(ta,td)] = D,  [td] = D,  [ta] = C in this application

        The stayed-at term becomes the FUNCTION, retyped to C: "ta becomes a
        question represented by C". The answer is the argument, and its entity
        is where the user now stands — the sense comes from outside, and the
        user takes the point of view of D.
        """
        act = inter.act
        stayed = subterm_at(inter.term, act.path)
        operand = self._operand(inter, opt)
        # Register the retype so its LABEL survives: the term stores only the
        # iri, and without this a question's name is lost the moment the step
        # is applied — it would render as a raw slug ever after.
        retype = self._register(inter, prop.retype)
        inter.term = replace_at(inter.term, act.path,
                                LamApp(func=stayed, arg=operand,
                                       func_type=retype))
        inter.pointers.after_contraction(act, option=1)
        # the user MOVES: the pointer now stands at the answer
        if opt.entity is not None:
            inter.pointer_entities[act.pid] = opt.entity
            moved = opt.entity.short()
        else:
            moved = opt.closed_name or "?"
        c = prop.retype.short() if prop.retype else "?"
        return f"[1] contraction opt.1 — [ta]={c}, moved → {moved}"

    # ── cases 4 and 5: leaving a point ────────────────────────────────────

    def skip(self, prop: Optional[Proposal] = None) -> None:
        """Case 4 — skip this point; its question type joins the lambdaList."""
        inter = self.current
        if inter is None or inter.act is None:
            return
        act = inter.act
        ent = (prop.retype if (prop and prop.retype)
               else inter.pointer_entities.get(act.pid))
        self._bind(ent)
        inter.pointers.skip(act)

    def resume(self) -> list[Entity]:
        """Case 5 — the user resumes the query.

        Every point still open is left, and its question type appended. Then
        the current interaction is closed and every subquery never started is
        appended too, so the interaction question binds all of it.
        """
        inter = self.current
        appended: list[Entity] = []
        if inter is not None:
            for p in inter.pointers.resume():
                ent = inter.pointer_entities.get(p.pid)
                if self._bind(ent):
                    appended.append(ent)
            self.close()
        for s in self.seeds:
            if s.iri not in self.started:
                self.started.add(s.iri)
                if self._bind(s):
                    appended.append(s)
        return appended

    def _bind(self, ent: Optional[Entity]) -> bool:
        """Append `ent` to the lambdaList unless it is already a binder."""
        if ent is None:
            return False
        if any(e.iri == ent.iri for e in self.lambda_list):
            return False
        self.lambda_list.append(ent)
        return True

    # ── closing ───────────────────────────────────────────────────────────

    def close(self, name: Optional[str] = None) -> Optional[ClosedInteraction]:
        """(t, P) -> t. A closed interaction has no pointer set."""
        inter = self.current
        if inter is None:
            return None
        c = ClosedInteraction(
            name=name or f"i{len(self.closed) + 1}",
            seed=inter.seed, subquery=inter.subquery,
            term=inter.term, steps=list(inter.steps),
        )
        self.closed.append(c)
        self.current = None
        return c

    def combined_term(self):
        """Every closed interaction of this query, as ONE term.

        The interactions are joined left to right, which keeps `[t]` the
        rightmost leaf — the type of the last thing read.
        """
        terms = [c.term for c in self.closed]
        if self.current is not None:
            terms.append(self.current.term)
        if not terms:
            return None
        t = terms[0]
        for nxt in terms[1:]:
            t = LamApp(func=t, arg=nxt)
        return t

    def interaction_question(self):
        """`lam E1...lam En.(t)` over the lambdaList.

        Built from the inside out, so E1 is the OUTERMOST binder and the list
        reads in the order the points were left. A binder must bind something:
        an entity that does not occur in the body is contracted in first, which
        is also what makes it a question ABOUT that entity.
        """
        t = self.combined_term()
        if t is None:
            return None
        if not self.lambda_list:
            return t
        for ent in reversed(self.lambda_list):
            var = self.entities.get(ent.iri, ent.label)
            if not _occurs(t, ent.iri):
                t = LamApp(func=t, arg=var)
            t = LamAbs(var=var, body=t)
        return t

    def done(self) -> bool:
        """No open interaction and no subquery left."""
        return self.current is None and self.next_subquery() is None


# ── helpers ───────────────────────────────────────────────────────────────


def _occurs(t, iri: str) -> bool:
    if isinstance(t, LamVar):
        return t.iri == iri
    if isinstance(t, LamApp) or hasattr(t, "func"):
        return _occurs(t.func, iri) or _occurs(t.arg, iri)
    if isinstance(t, LamAbs):
        return t.var.iri == iri or _occurs(t.body, iri)
    return False


def _copy(t, reg: EntityRegistry):
    """A copy of `t` whose entities are resolved through `reg`.

    Used to graft a closed interaction as an answer: the graft shares ENTITIES
    with its new home (same registry) but not structure, so closing stays final.
    """
    if isinstance(t, LamVar):
        return reg.get(t.iri, t.label)
    if isinstance(t, LamAbs):
        return LamAbs(var=reg.get(t.var.iri, t.var.label),
                      qid=t.qid, body=_copy(t.body, reg))
    if hasattr(t, "func"):
        ft = getattr(t, "func_type", None)
        return LamApp(func=_copy(t.func, reg), arg=_copy(t.arg, reg),
                      func_type=ft)
    return t
