"""The interaction engine — the loop, headless.

One query becomes one `QuerySession`. The query is decomposed into SUBQUERIES,
each mapped to an entity; each subquery is read by its own `Interaction`, which
is a term plus a pointer set. When no unclear point is left, the interaction is
CLOSED — (t, P) -> t — and the next subquery is taken up. When no subquery is
left, the session builds the INTERACTION QUESTION and is done.

    decompose query -> subqueries, each mapped to an entity
    loop:
        point = the unclear point of the current subquery
        if point:  case 1 | case 2 | case 3 | skip(4) | resume(5)
        else:      close; take the next subquery; exit when none left

NO I/O HERE. Nothing in this module talks to a model, a database or a terminal.
It takes proposals in and hands state back, so the whole loop is unit-testable
without spending a token — which is also what lets the web layer be a thin shell
over it. The delegate that invents questions and options sits behind
`StepSource`; the mock in the tests satisfies it.

THE LAMBDALIST. The interaction question is `lam E1...lam En.(t)`, and the
binders are the points nobody settled:

    case 4  skipping a case 1 or case 2 point appends its question type
    case 5  resuming appends every point still open
    -       a subquery never started appends its entity
    -       a skipped REFLECTION appends NOTHING: case 3 forks a pointer rather
            than leaving a question unanswered, so there is no question type

Order is the order they were left in, and one entity binds once however often it
was left (a repeated binder would bind nothing the second time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from optimal_lambda import LamAbs, LamVar
from interaction_state import (
    EntityRegistry, LamApp, LamFan, Path, Pointer, PointerSet,
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

        case 1   `entity` is the ANSWER (D); the user moves there.
                 `closed_name` instead names an already-closed interaction,
                 which the spec admits as an answer.
        case 2   `entity` is the QUESTION (A); the user stays.
        case 3   `entity_a` / `entity_b` are the question and the answer, held
                 against one another.
    """
    label: str
    rationale: str = ""
    entity: Optional[Entity] = None
    entity_a: Optional[Entity] = None
    entity_b: Optional[Entity] = None
    closed_name: Optional[str] = None


@dataclass
class Proposal:
    """What is offered at one point.

        case  1 | 2 | 3 | 0        0 means "no unclear point here"
        point the words of the subquery at issue
        question  the question put to the user (cases 1 and 3)
        options   up to 4; a choice needs at least 2
        retype    C — what the function-position term is typed as at the
                  application this step builds (cases 1 and 2)
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
    # pid -> the entity that pointer stands at. Navigation uses this, NOT a
    # reflection's cast.
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
        # A single option is not a choice.
        if p.case in (1, 2, 3) and len(p.options) < 2:
            return Proposal(case=0)
        return p

    def apply(self, prop: Proposal, opt: Option) -> str:
        """Apply the chosen option. Returns the calculus line."""
        inter = self.current
        if inter is None or inter.act is None:
            raise ValueError("no open interaction")
        if prop.case == 1:
            line = self._case1(inter, prop, opt)
        elif prop.case == 2:
            line = self._case2(inter, prop, opt)
        elif prop.case == 3:
            line = self._case3(inter, prop, opt)
        else:
            raise ValueError(f"not an applicable case: {prop.case}")

        answer = opt.label
        if prop.case == 3 and opt.entity_a is not None:
            answer = (f"{opt.label} — {opt.entity_a.short()} (question) · "
                      f"{opt.entity_b.short()} (answer)")
        inter.steps.append(Step(case=prop.case, point=prop.point,
                                question=prop.question, answer=answer,
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

    def _case2(self, inter: Interaction, prop: Proposal, opt: Option) -> str:
        """Contraction option 2 — app(a, tb); the user STAYS.

            [app(a,tb)] = B,  [a] = A in this application

        The current interaction is the ANSWER; the option is the question it
        answers. So the operand becomes the FUNCTION, retyped to A, and the
        stayed-at term is the argument. The pointer does not move: the user is
        still at B.
        """
        act = inter.act
        stayed = subterm_at(inter.term, act.path)
        operand = self._operand(inter, opt)
        # For case 2 the retype names what the OPERAND is in this application —
        # the question A the current interaction answers.
        retype = self._register(inter, prop.retype)
        if retype is None and opt.entity is not None:
            retype = opt.entity.iri
        inter.term = replace_at(inter.term, act.path,
                                LamApp(func=operand, arg=stayed,
                                       func_type=retype))
        inter.pointers.after_contraction(act, option=2)
        # the user STAYS: pointer_entities is unchanged
        asked = opt.entity.short() if opt.entity else (opt.closed_name or "?")
        return f"[2] contraction opt.2 — reached from {asked}"

    def _case3(self, inter: Interaction, prop: Proposal, opt: Option) -> str:
        """Reflection — app(tc, tc) over ONE shared tc.

            P = P - {actPtr} ∪ {left, right},  both [tc] = C

        The point is seen as a (question, answer) tuple. The interaction forks
        into the context of A and the context of B; both occurrences are the
        same node, and both still type as C. The casts record which side is the
        question and which the answer.
        """
        act = inter.act
        here = inter.here()
        stayed = subterm_at(inter.term, act.path)
        ea, eb = opt.entity_a, opt.entity_b
        # Register both casts: like a retype, a cast is stored on the fan as an
        # iri only, so its label would otherwise be lost.
        self._register(inter, ea)
        self._register(inter, eb)
        inter.term = replace_at(inter.term, act.path,
                                LamFan(principal=stayed,
                                       grey_cast=ea.iri, black_cast=eb.iri))
        left, right = inter.pointers.after_reflection(act, ea.iri, eb.iri)
        # Navigation uses the shared subject's own entity, not the cast: both
        # occurrences stand at C.
        inter.pointer_entities.pop(act.pid, None)
        inter.pointer_entities[left.pid] = here
        inter.pointer_entities[right.pid] = here
        return (f"[3] reflection — {ea.short()} (question) · "
                f"{eb.short()} (answer)")

    # ── cases 4 and 5: leaving a point ────────────────────────────────────

    def skip(self, prop: Optional[Proposal] = None) -> None:
        """Case 4 — skip this point.

        Skipping a case 1 or case 2 point appends its question type to the
        lambdaList. Skipping a REFLECTION appends nothing: case 3 forks rather
        than leaving a question unanswered.
        """
        inter = self.current
        if inter is None or inter.act is None:
            return
        act = inter.act
        # A reflection's occurrence binds nothing — by the case, and also when
        # no proposal is given, by where the pointer came from.
        reflected = act.origin in ("reflect-left", "reflect-right")
        if (prop is None and not reflected) or (prop is not None
                                                and prop.case in (1, 2)):
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
                # A REFLECTION'S OCCURRENCE BINDS NOTHING, for the same reason a
                # skipped reflection does not: case 3 forks the interaction
                # rather than leaving a question unanswered, so there is no
                # question type to bind. Without this, resuming after a
                # reflection would report both of its occurrences as "left
                # open" when the user had in fact settled that point.
                if p.origin in ("reflect-left", "reflect-right"):
                    continue
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
    if isinstance(t, LamFan):
        return (_occurs(t.principal, iri)
                or _occurs(t.grey_ctx, iri) or _occurs(t.black_ctx, iri))
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
    if isinstance(t, LamFan):
        return LamFan(principal=_copy(t.principal, reg),
                      grey_ctx=_copy(t.grey_ctx, reg),
                      black_ctx=_copy(t.black_ctx, reg),
                      grey_cast=t.grey_cast, black_cast=t.black_cast)
    if isinstance(t, LamAbs):
        return LamAbs(var=reg.get(t.var.iri, t.var.label),
                      qid=t.qid, body=_copy(t.body, reg))
    if hasattr(t, "func"):
        ft = getattr(t, "func_type", None)
        return LamApp(func=_copy(t.func, reg), arg=_copy(t.arg, reg),
                      func_type=ft)
    return t
