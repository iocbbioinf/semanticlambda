"""The ontology layer — notes/reading_desc §8.

An ONTOLOGY is a model in which a reading is valid: a term WHOSE REDUCTION
REPLAYS THE READING. Each reading carries a SET of them — every hypothesis still
consistent with what the user has done.

    contraction in the reading  <->  RULE 1 fires  (beta-step)
    reflection  in the reading  <->  RULE 4 fires  (duplication)

    ont( G(t), P )      P corresponds ELEMENTWISE to the reading's pointer set

The division of labour (§8.1, and the `reading-interpretation` skill): a READING
is subjective and contains NO abstractions (I6); an ONTOLOGY is the EXPLICIT layer
and is built from the POSITIVE FORM of abstractions, which a reading never sees.

WHAT THIS MODULE DOES NOT DO. It does not run the GAL bus reducer. §8 is stated
over shared graphs and rule numbers, but every operation it actually requires —
"is rule 1 applicable at p", "apply rule 1 then all silent reductions" — is at
this scale a statement about the TERM: rule 1 applies exactly where an
abstraction meets an application, and firing it is beta-reduction at that
position. Silent reductions (§8.5(3)) are delimiter shuffles and non-syntactic
sharing, which have no term-level effect at all. So the layer is implemented on
terms, and `optimal_lambda.normalize` is not called. The correspondence is
recorded here rather than assumed: if the ontology layer ever needs the bus
graph — for context semantics, say — this is the seam to revisit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from optimal_lambda import LamTerm, LamVar, LamApp, LamAbs
from kg_store import load_lambda_db, local_name
from reading_state import (
    Path, LamFan, HOLE, EntityRegistry, subterm_at, replace_at,
    lam_to_dict_shared, lam_from_dict_shared,
)

# NO BOUND ON THE ONTOLOGY SET. Enrichment branches over every abstraction of
# matching type at every pointer on every step, and option 1's closed-reading
# case multiplies by |cr.ontologies| (§8.6 RN4); against that the outer
# structural test refutes heavily (§8.8). Whether the rates balance is O7 —
# unsettled — and a cap was applied while it stayed open.
#
# THE CAP WAS THE WRONG INSTRUMENT, and measurement said so. On a two-step
# reading enrichment produced 327 candidates at step 1, of which 304 still held
# an abstraction. A limit of 64 discarded 80% of them at the one moment nothing
# distinguishes which will pay off — every candidate has fired at most once —
# and the one that would have reached fired=2 on the NEXT step was among the
# dropped. Ranking by "still live" did not help either: almost everything is
# live at that point.
#
# So the set is now KEPT WHOLE. An ontology leaves it only by REFUTATION, which
# is what §8.8 says: refutation is by omission from the update rules, never by
# eviction. A cap made the layer lose models for a reason the calculus does not
# have. O7 remains open — if the set grows unmanageably that is now VISIBLE
# rather than silently truncated, which is the honest failure mode.
MAX_ONTOLOGIES = None


@dataclass
class Ontology:
    """ont(G(t), P) — §8.1.

    term      a term that MAY contain abstractions (unlike a reading)
    pointers  pid -> path, keyed by the READING's pointer ids so the elementwise
              correspondence of §8.1 is explicit rather than positional
    fired     how many times a rule actually fired in this ontology, i.e. how
              often its hypotheses paid off (O8: unruled, so recorded but only
              used for eviction ordering)
    is_reading  this member IS the reading itself — see `reading_ontology`.
    origin    THE GRAPH AS PROPOSED — this ontology's term with every
              enrichment substitution in place but NO reduction applied. None
              while nothing has fired yet, in which case `term` IS the origin;
              `origin_term` resolves that.

              Why it is kept: an ontology is a HYPOTHESIS, and `term` is that
              hypothesis after the reading has consumed part of it. What was
              proposed and what is left are different graphs, and only the
              former shows which questions the model actually claimed — rule 1
              consumes the abstraction it fires on, so the resolved term has
              lost it.

              IT TRACKS ENRICHMENT, NOT ONLY THE FIRST FIRING. Reduction is
              recorded once (the graph before it) and never re-derived, since
              reduction is not invertible here. But a question substituted by
              enrichment AFTER that firing is still part of the proposal, so
              `_enrich_origin` carries each such substitution into the origin as
              well. Otherwise the resolved term would contain entities the
              origin never mentions, and the ORIGINAL view could not say which
              question introduced them.
    """
    term: LamTerm
    pointers: dict[int, Path] = field(default_factory=dict)
    fired: int = 0
    is_reading: bool = False
    origin: Optional[LamTerm] = None

    def corresponding(self, pid: int) -> Optional[Path]:
        """The counterpart of the reading's pointer `pid` — §8.1."""
        return self.pointers.get(pid)

    @property
    def origin_term(self) -> LamTerm:
        """The graph this ontology was proposed as, before any reduction.

        Equals `term` until something fires, so callers need not special-case
        an unreduced ontology.
        """
        return self.term if self.origin is None else self.origin

    @property
    def is_reduced(self) -> bool:
        """Has the reading actually reduced this ontology?"""
        return self.origin is not None

    def to_dict(self) -> dict:
        d = {
            "term": lam_to_dict_shared(self.term),
            "pointers": {str(k): list(v) for k, v in self.pointers.items()},
            "fired": self.fired,
            "is_reading": self.is_reading,
        }
        if self.origin is not None:
            d["origin"] = lam_to_dict_shared(self.origin)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Ontology":
        raw = d.get("origin")
        return Ontology(
            term=lam_from_dict_shared(d["term"]),
            pointers={int(k): tuple(v) for k, v in d.get("pointers", {}).items()},
            fired=d.get("fired", 0),
            is_reading=d.get("is_reading", False),
            # absent in records written before origins were kept: such an
            # ontology simply has no recoverable pre-reduction graph.
            origin=lam_from_dict_shared(raw) if raw else None,
        )

    def __str__(self) -> str:
        return str(self.term)


# ── abstractions: the positive form  (§8.2) ─────────────────────────────────

def load_abstractions() -> list[LamAbs]:
    """Every saved abstraction, as its POSITIVE FORM G(lam a.t).

    A reading sees only TITLES; ontologies are built from these.

    ENTITY SHARING IS REBUILT PER ABSTRACTION, one registry each. An abstraction
    body naming one entity twice names ONE entity, so it must be ONE node reached
    twice — a SHARED subterm (§1) — exactly as for a reading. This matters to
    §8.7, whose outer test asks whether G(toc) is already shared in both sides of
    an application, and sharing there is node identity, not structural equality.
    Loading these bodies without a registry would answer "no" to every candidate
    for the wrong reason: the graph would have the sharing, the objects would not.

    A registry PER ABSTRACTION, not one global: two different abstractions
    mentioning the same entity are two separate graphs, and fusing their nodes
    would invent sharing nobody asserted.
    """
    out: list[LamAbs] = []
    for rec in load_lambda_db():
        d = rec.get("term")
        if not isinstance(d, dict) or d.get("type") != "abs":
            continue
        try:
            t = lam_from_dict_shared(d, EntityRegistry())
        except (ValueError, KeyError):
            continue
        if isinstance(t, LamAbs):
            out.append(t)
    return out


def abstraction_type(q: LamAbs) -> str:
    """[G(lam a.t)] == [a] — the BOUND VARIABLE's type, NOT the body's (§8.2).

    Abstracting RETYPES, which is what makes "all abstractions of type A" a
    finite lookup instead of a search.
    """
    return q.var.iri


def abstractions_of_type(iri: str,
                         pool: Optional[list[LamAbs]] = None) -> list[LamAbs]:
    pool = load_abstractions() if pool is None else pool
    return [q for q in pool if abstraction_type(q) == iri]


def abstraction_title(q: LamAbs) -> str:
    """The negative form's display name — what a reading would see (§8.2)."""
    return local_name(q.var.iri)


# ── rule 1: where a beta-step is available  (§8.5(2), §8.5(3)) ──────────────

def _bare(t):
    """See through a sharing fan to the subject it shares (§4.2)."""
    while isinstance(t, LamFan):
        t = t.principal
    return t


def rule1_applicable(term: LamTerm, path: Path) -> bool:
    """True iff RULE 1 can fire at `path` — i.e. the subterm there is an
    ABSTRACTION standing in function position of an application (§8.6).

    This is the NON-SILENT case of §8.5(3): on the rightmost wire the two fans
    are the syntactic lambda and @, so the reduction is a real beta-step and
    needs the user's act. That act is the reading's contraction.
    """
    return isinstance(_bare(subterm_at(term, path)), LamAbs)


def _substitute(body: LamTerm, var_iri: str, value: LamTerm) -> LamTerm:
    """Capture-free enough for this layer: entities are globally unique IRIs, so
    a bound variable is identified by its IRI and cannot be shadowed by a
    different entity of the same name."""
    if isinstance(body, LamVar):
        return value if body.iri == var_iri else body
    if isinstance(body, LamFan):
        return LamFan(
            principal=_substitute(body.principal, var_iri, value),
            grey_ctx=_substitute(body.grey_ctx, var_iri, value),
            black_ctx=_substitute(body.black_ctx, var_iri, value),
            grey_cast=body.grey_cast, black_cast=body.black_cast,
        )
    if isinstance(body, LamApp):
        return LamApp(func=_substitute(body.func, var_iri, value),
                      arg=_substitute(body.arg, var_iri, value))
    if isinstance(body, LamAbs):
        if body.var.iri == var_iri:
            return body                     # inner binder shadows this one
        return LamAbs(var=body.var, body=_substitute(body.body, var_iri, value))
    return body                              # HOLE and anything else


def apply_rule1(term: LamTerm, app_path: Path) -> Optional[LamTerm]:
    """Fire RULE 1 at the APPLICATION at `app_path`: (lam a. body) arg -> body[a:=arg].

    Then "all possible silent reductions" (§8.5(3)) — which at term level are a
    no-op, being delimiter shuffles and non-syntactic sharing. Returns None if
    there is no redex there.
    """
    node = _bare(subterm_at(term, app_path))
    if not isinstance(node, LamApp):
        return None
    fn = _bare(node.func)
    if not isinstance(fn, LamAbs):
        return None
    reduced = _substitute(fn.body, fn.var.iri, node.arg)
    return replace_at(term, app_path, reduced)


def substituted_position(term: LamTerm, app_path: Path) -> Optional[Path]:
    """Where the argument LANDS when rule 1 fires at `app_path`.

    The path, in the REDUCED term, of the position the substitution filled — i.e.
    where the abstraction's bound variable stood in its body. This is where the
    ontology's pointer belongs after a firing: the reading's own actPtr goes to
    tb, the B side, and tb's counterpart in the ontology is the B-typed material
    the substitution just put in place, NOT the root of the reduct.

    Pointing at the reduct's root instead strands the pointer: the next step's
    redex is then wherever the question's body left it, typically a level or two
    below, and §8.6 — which tests only AT the pointer — refutes on depth rather
    than on content.

    RULE 1 IS LINEAR, so there is one such position and no choice to make. A
    question whose body names its entity twice names ONE shared node (§1), which
    a reflection (rule 4) duplicates; rule 1 substitutes at that single place.
    Where the body genuinely has several distinct occurrences the FIRST in
    left-to-right order is taken, so the result stays deterministic.

    Returns None if there is no redex at `app_path`, or if the bound variable does
    not occur in the body (a question that binds nothing — refused by §8.2 Q1).
    """
    node = _bare(subterm_at(term, app_path))
    if not isinstance(node, LamApp):
        return None
    fn = _bare(node.func)
    if not isinstance(fn, LamAbs):
        return None
    iri = fn.var.iri

    def find(t: LamTerm, path: Path) -> Optional[Path]:
        """The first free occurrence of `iri` in the abstraction's body."""
        if isinstance(t, LamVar):
            return path if t.iri == iri else None
        if isinstance(t, LamFan):
            return find(t.principal, path)
        if isinstance(t, LamApp):
            return find(t.func, path + (0,)) or find(t.arg, path + (1,))
        if isinstance(t, LamAbs):
            if t.var.iri == iri:
                return None                 # shadowed: not this binder's variable
            return find(t.body, path + (0,))
        return None

    inner = find(fn.body, ())
    return None if inner is None else app_path + inner


# ── enrichment  (§8.4) ──────────────────────────────────────────────────────

def _parent_app(term: LamTerm, path: Path) -> Optional[tuple[Path, int]]:
    """(path of the enclosing application, which side `path` is on), or None."""
    if not path:
        return None
    parent = path[:-1]
    node = subterm_at(term, parent)
    if isinstance(_bare(node), LamApp):
        return (parent, path[-1])
    return None


def _enrich_origin(origin: Optional[LamTerm], var: LamVar,
                   q: LamAbs) -> Optional[LamTerm]:
    """Carry an enrichment substitution into the ORIGIN graph as well.

    THE ORIGIN MUST SHOW EVERY SUBSTITUTION, not only the ones made before the
    first firing. `origin` is the graph the ontology was PROPOSED as (§8.1), and
    a question enrichment substitutes later is part of that proposal — it is
    material the model claims. Freezing the origin at the first firing loses it:
    the resolved term then contains an entity the origin never mentions, and the
    ORIGINAL view cannot show which question put it there.

    The substitution is BY IRI, which is exactly how `_substitute` identifies a
    variable: entities are globally unique here, so every free occurrence of that
    entity in the origin is the same variable enrichment replaced in the term.

    Returns None when there is no origin yet — nothing has fired, so `term` IS
    the origin and the caller's `new_term` already carries the substitution.
    """
    if origin is None:
        return None
    return _substitute(origin, var.iri, q)


def enrich(ontologies: list[Ontology],
           pool: Optional[list[LamAbs]] = None,
           named: Optional[dict[int, LamAbs]] = None) -> list[Ontology]:
    """Substitute the positive form of abstractions at pointed variables (§8.4).

    Returns the set GROWN by every candidate the rule admits.

    THE SNAPSHOT IS LOAD-BEARING: iteration is over the set as it stood before
    the pass. Without it, newly created ontologies are re-enriched in the same
    pass and — since each substitution can create fresh matching material — the
    pass need not terminate.

    TERMINATION: recursion is strictly on the SHRINKING pointer set, so no
    abstraction is ever substituted inside one introduced in the same pass.
    Depth is bounded by |P|.

    `named` maps a reading pointer id to THE abstraction the user named there (a
    title operand): the quantifier collapses to that one instead of branching
    over all of matching type (§8.4).
    """
    pool = load_abstractions() if pool is None else pool
    named = named or {}
    out = list(ontologies)

    def candidates(pid: int, v: LamVar) -> list[LamAbs]:
        if pid in named:
            return [named[pid]]
        return abstractions_of_type(v.iri, pool)

    def go(o: Ontology, pids: list[int]) -> None:
        if not pids:
            return
        pid, rest = pids[0], pids[1:]
        go(o, rest)                          # the remaining pointers

        path = o.pointers.get(pid)
        if path is None:
            return
        tp = _bare(subterm_at(o.term, path))
        parent = _parent_app(o.term, path)
        if parent is None:
            return
        ppath, side = parent
        papp = _bare(subterm_at(o.term, ppath))

        if side == 0 and isinstance(tp, LamVar):
            # (a) tp in FUNCTION position — the substituted abstraction becomes a
            #     NEW POSITION: pqp replaces p.
            for q in candidates(pid, tp):
                new_term = replace_at(o.term, path, q)
                ptrs = dict(o.pointers)
                ptrs[pid] = path             # the pointer follows the material
                # Enrichment SUBSTITUTES, it does not reduce, so `fired` is
                # inherited — but THE ORIGIN TAKES THE SUBSTITUTION TOO, so the
                # ORIGINAL view shows every question the model claims, not only
                # those substituted before the first firing.
                cand = Ontology(term=new_term, pointers=ptrs, fired=o.fired,
                                origin=_enrich_origin(o.origin, tp, q))
                out.append(cand)
                go(cand, rest)
        elif side == 1:
            # (b) tp in ARGUMENT position, function side a variable — the
            #     abstraction replaces material in FUNCTION position, so the
            #     pointer set is left as P.
            fn = _bare(papp.func) if isinstance(papp, LamApp) else None
            if isinstance(fn, LamVar):
                fpath = ppath + (0,)
                for q in candidates(pid, fn):
                    new_term = replace_at(o.term, fpath, q)
                    cand = Ontology(term=new_term, pointers=dict(o.pointers),
                                    fired=o.fired,
                                    origin=_enrich_origin(o.origin, fn, q))
                    out.append(cand)
                    go(cand, rest)

    for o in list(ontologies):               # SNAPSHOT
        go(o, list(o.pointers.keys()))
    return out


# ── the reading is a member of its own ontology set ─────────────────────────

def reading_ontology(term: LamTerm, pointers: dict[int, Path]) -> Ontology:
    """The reading itself, as a member of its own ontology set.

    A reading is trivially a model of itself: it contains exactly the material
    the user built, so every structural test about that material holds by
    construction. It never FIRES, though — a reading has no abstractions (I6), so
    nothing in it can reduce and it explains nothing. It is the degenerate model
    that never contradicts and never accounts for anything, which is why `fired`
    stays 0 and O8's ranking sorts it below any confirmed hypothesis.

    Keeping it in the set means the set is never empty while a reading exists, so
    an empty set can only mean the reading itself is gone. It is maintained by
    MIRRORING the reading rather than by §8.6/§8.7, which test candidate models
    against a step: there is nothing to test here, since this member IS the step.
    """
    return Ontology(term=term, pointers=dict(pointers), fired=0, is_reading=True)


def sync_reading_ontology(ontologies: list[Ontology], term: LamTerm,
                          pointers: dict[int, Path]) -> list[Ontology]:
    """Put the reading's own current state back into the set, in place.

    Called after every reading step, INSTEAD of putting the reading member
    through §8.6/§8.7 — it is not a hypothesis to be tested but a mirror of what
    the user did, so it is simply refreshed. Any other member that happens to
    equal the reading is left alone; only the flagged one is replaced.
    """
    out = [o for o in ontologies if not o.is_reading]
    out.insert(0, reading_ontology(term, pointers))
    return out


def point_reading_mirror_at_tb(ontologies: list[Ontology], pid: int) -> list[Ontology]:
    """Put the reading's mirror's pointer on tb — the B side of the step.

    THE THIRD PART OF THE CONTRACTION UPDATE (§8.6). The mirror's pointer is not
    left on the application the step built but DESCENDS to its tb, in BOTH
    options: §4.1 makes tb the B side either way, so [actPtr] stays equal to the
    reading's own type, and the pointer moves INTO the term as the reading grows
    instead of sitting on the root for ever.

    WHY IT MATTERS: a pointer pinned at the root can only ever match material at
    the root, so an ontology whose corresponding application sits NESTED is
    refuted for its depth rather than for its content. Descending to tb is what
    keeps the reader's position and the term's growth in step.

    actPtr and the pointer set move together: the entry for `pid` IS actPtr's
    counterpart, so re-pointing it re-points both (§8.1's elementwise
    correspondence).
    """
    out: list[Ontology] = []
    for o in ontologies:
        if not o.is_reading:
            out.append(o)
            continue
        ptrs = dict(o.pointers)
        p = ptrs.get(pid)
        if p is not None:
            tb = p + (1,)
            # only descend where tb exists: a step that did not leave an
            # application there has nothing to descend into.
            if subterm_at(o.term, tb) is not None:
                ptrs[pid] = tb
        out.append(Ontology(term=o.term, pointers=ptrs, fired=o.fired,
                            is_reading=True, origin=o.origin))
    return out


def strip_reading_ontology(ontologies: list[Ontology]) -> list[Ontology]:
    """The candidate models only, without the reading's own mirror.

    Used before an update: the mirror must not be run through the update rules.
    """
    return [o for o in ontologies if not o.is_reading]


# ── initialisation  (§8.3) ─────────────────────────────────────────────────

def init_from_type(var: LamVar, pid: int,
                   pool: Optional[list[LamAbs]] = None) -> list[Ontology]:
    """After init form (a): ontologies = { ont(G(a), {p}) }, then enrich (§8.3).

    Only the seed. Abstractions cannot usefully be proposed here: an ontology of
    a contraction must present an app(ta, tb) at the pointer with tb the entity
    the step reaches, and that entity is not known until the user chooses it.
    Proposing candidates is therefore the FIRST STEP's job — see
    `candidates_for_contraction`.
    """
    return enrich([Ontology(term=var, pointers={pid: ()})], pool=pool)


def candidates_for_contraction(pid: int, path: Path, a_iri: str, b_iri: str,
                               operand: LamTerm,
                               pool: Optional[list[LamAbs]] = None,
                               ) -> list[Ontology]:
    """Ontologies proposed BY a contraction, from the saved abstractions (§8.4).

    §8.4's own two cases need the pointed variable to sit inside an application,
    which is not so at the start of a reading — and since enrichment only ever
    ADDS to what a set already holds, a set that begins with nothing
    substitutable would stay closed for the whole reading, never offering the
    models the layer holds.

    So a contraction also PROPOSES: for each saved abstraction whose type is the
    type at the pointer, the candidate app(q, operand). That is a RULE 1 REDEX
    whose firing IS this very contraction, which is what §8.1 asks of an
    ontology — that its reduction replay the reading. The operand is the one the
    user just chose, which is why this cannot be done at init.

    Only abstractions that actually FIT are kept: [q] must be the type at the
    pointer, and the reduct must present the step's own types. The rest would be
    refuted immediately, so they are never proposed.

    WHICH SIDE THE REDEX GOES ON DEPENDS ON THE OPTION. Both options build
    app(t1, t2) with [t1] == A and [t2] == B (§4.1), and rule 1 fires at t1 — so
    the proposal is always app(q, x) with [q] == A. What differs is WHAT THE OTHER
    SIDE IS:

      option 1  the reader MOVES to B: the operand is t2, so the candidate is
                app(q, operand) with q of type A — the type AT THE POINTER.
      option 2  the reader STAYS: the operand is t1, so the ABSTRACTION MUST BE OF
                THE OPERAND'S OWN TYPE and the pointed material is t2. The
                candidate is app(q, stayed) with [q] == A == [operand].

    Getting this wrong empties the set on every option-2 step: the proposals would
    all have [t1] equal to the type at the pointer, which under option 2 is B, and
    the outer test demands A there.

    THE POINTER DESIGNATES A SIDE, NOT THE APPLICATION. §8.6 reads the app(ta,tb)
    ENCLOSING the pointer, so a proposal must point INTO the application it
    builds: here at tb, which is the operand — the reader has moved to B.
    """
    out: list[Ontology] = []
    for q in abstractions_of_type(a_iri, pool):
        cand = LamApp(q, operand)
        out.append(Ontology(term=cand, pointers={pid: path + (1,)}))
    return out


def candidates_for_option2(pid: int, path: Path, a_iri: str,
                           stayed: LamTerm,
                           pool: Optional[list[LamAbs]] = None,
                           ) -> list[Ontology]:
    """Proposals for an OPTION 2 contraction (§4.1, §8.6).

    Option 2 puts the OPERAND in function position: the reading builds
    app(operand, stayed) with [operand] == A and [stayed] == B, and the reader
    does not move. Rule 1 fires at t1, so the abstraction to propose is of type
    A — the OPERAND's type, not the type at the pointer — applied to the material
    the reader stayed at.

    The pointer designates a SIDE (§8.6 reads the enclosing application), and
    under option 2 the reader stays at B — which is tb, the `stayed` material.
    """
    out: list[Ontology] = []
    for q in abstractions_of_type(a_iri, pool):
        out.append(Ontology(term=LamApp(q, stayed),
                            pointers={pid: path + (1,)}))
    return out


def steps_proposed(ontologies: list[Ontology], pid: int, type_at,
                   reverse: bool = False) -> dict[str, list[Ontology]]:
    """Entities the ONTOLOGY SET offers as a next contraction at actPtr.

    An ontology is a model in which the reading is valid, so material it already
    contains at the reader's own position is a step the reading COULD take — one
    for which a model exists, even where the knowledge graph asserts no claim.
    This reads those steps off the set:

      forward (reverse=False, option 1)
          some ont(G(t), P) has a subgraph app(ta, tb) with P's counterpart of
          actPtr pointing at ta; the entity offered is B == [tb].
      reverse (reverse=True, option 2)
          ... pointing at tb; the entity offered is A == [ta].

    Returns entity iri -> the ontologies proposing it, so the caller can say HOW
    MANY models back each step and name them.

    WHERE THE POINTER SITS. After a contraction actPtr designates the APPLICATION
    NODE it just built (§4.1, `PointerSet.after_contraction`) — not one of its
    sides. So the app(ta, tb) to read is the one AT the pointer, and ta, tb are
    its two branches. The pointer "points to ta" in the sense of §4.1 option 1:
    ta is the material the reader stands at, tb the answer alongside it.

    An ontology whose pointer designates something that is NOT an application
    proposes nothing HERE — but see `steps_from_questions`, which covers the one
    position where that is the normal case rather than the exception: the start of
    a reading, where §8.3 seeds a bare variable and no application exists yet.
    """
    out: dict[str, list[Ontology]] = {}
    for o in ontologies:
        path = o.corresponding(pid)
        if path is None or not path:
            continue
        # THE POINTER DESIGNATES A SIDE, so the application is its PARENT — the
        # same convention §8.6 now uses. The entity offered is the OTHER side.
        app_path = path[:-1]
        node = _bare(subterm_at(o.term, app_path))
        if not isinstance(node, LamApp):
            continue
        # forward (option 1): the reader stands at ta; tb is the entity offered
        # reverse (option 2): the reader stands at tb; ta is the entity offered
        other = app_path + (0 if reverse else 1,)
        iri = type_at(o.term, other)
        if not iri:
            continue
        out.setdefault(iri, []).append(o)
    return out


def steps_from_questions(type_iri: str, type_at,
                        reverse: bool = False,
                        pool: Optional[list[LamAbs]] = None,
                        ) -> dict[str, list[LamAbs]]:
    """Steps the SAVED QUESTIONS offer at a position of type `type_iri`.

    §8.7b read the offers off the ontology SET, which presupposes an ontology
    holding an application at the pointer. At the START of a reading there is
    none: §8.3 seeds a bare variable, so nothing is offered and a reading can
    only begin along a real claim — even where a question exists that would
    license the step.

    THIS IS THE SAME OFFER, ONE STEP EARLIER, read off the questions themselves
    rather than off the set. For each saved question q of type `type_iri`, its
    BODY is the material the question is about; wherever that body contains
    app(ta, tb) with the reader's own type on one side, the other side is a step
    the question licenses:

        forward  [ta] == type_iri  ->  offer B == [tb]
        reverse  [tb] == type_iri  ->  offer A == [ta]

    Taking such a step then makes `candidates_for_contraction` propose app(q, B),
    a rule-1 redex whose firing IS that contraction (§8.4) — so the offer and the
    model that justifies it are the same question.

    Returns entity iri -> the questions offering it.
    """
    out: dict[str, list[LamAbs]] = {}
    for q in abstractions_of_type(type_iri, pool):
        def walk(t):
            node = _bare(t)
            if isinstance(node, LamApp):
                ta, tb = node.func, node.arg
                mine, other = (tb, ta) if reverse else (ta, tb)
                if type_at(mine, ()) == type_iri:
                    iri = type_at(other, ())
                    if iri and iri != type_iri:
                        out.setdefault(iri, [])
                        if q not in out[iri]:
                            out[iri].append(q)
                walk(node.func)
                walk(node.arg)
            elif isinstance(node, LamAbs):
                walk(node.body)
        walk(q.body)
    return out


def init_from_closed(stored: list[Ontology], pid: int) -> list[Ontology]:
    """After init form (b): the STORED ontologies of the closed reading (§8.3).

    Not re-derived — they are survivors of refutation over that reading's whole
    construction, which enrichment cannot recover (§1). Their pointers collapse
    to the single ROOT pointer, mirroring what closing did to the reading's own.
    """
    rebased = [Ontology(term=o.term, pointers={pid: ()}, fired=o.fired,
                        origin=o.origin)
               for o in stored]
    return enrich(rebased)


# ── update on a contraction  (§8.6) ────────────────────────────────────────

def after_contraction(ontologies: list[Ontology], pid: int, option: int,
                      a_iri: str, b_iri: str,
                      type_at,
                      operand_ontologies: Optional[list[Ontology]] = None,
                      ) -> tuple[list[Ontology], dict]:
    """Update the set for a contraction the user just performed (§8.6).

    TWO BRANCHES per ontology, and the distinction is the point:

      RULE 1 FIRES      the ontology's hypothesis paid off — reduce, advance.
      RULE 1 DOES NOT   the ontology proposed nothing at this position. It is
                        KEPT: that is SILENCE, not contradiction.

    REFUTATION is by omission — an ontology that fails the OUTER STRUCTURAL TEST
    is simply never added (§8.8). That test is the strong gate: it demands the
    ontology ALREADY CONTAIN the material the reading is about to build, and
    since enrichment substitutes abstractions but never adds applications, an
    ontology passes only if some abstraction BODY supplies the needed position.

    `type_at(term, path)` gives the type at a position, so this module does not
    duplicate the typing rules.

    Returns (new set, stats) where stats reports what fired and what was refuted.
    """
    out: list[Ontology] = []
    stats = {"fired": 0, "silent": 0, "refuted": 0, "grafted": 0}

    for o in ontologies:
        path = o.corresponding(pid)
        if path is None:
            stats["refuted"] += 1
            continue

        # ── the outer structural test (§8.6, §8.8) ──────────────────────────
        # THE POINTER DESIGNATES tb, SO THE APPLICATION IS ITS PARENT. p points
        # at the B side — the place the reader stands — and the app(ta, tb) the
        # step is about is the one ENCLOSING it. Testing the pointed subterm
        # instead asks a variable to be an application and refutes every
        # candidate whose redex sits, correctly, one level up.
        #
        # This also makes §8.6 and §8.7 read positions the SAME way: §8.7's
        # second branch already looks for its sharing fan ABOVE the pointer
        # (`_sharing_fan_above`), for exactly this reason.
        #
        # [OPEN — O9] The test still reads the ontology AS IT STANDS, not as the
        # step would leave it; that question is untouched here.
        if not path:
            # the root has no enclosing application, so there is nothing to test
            stats["refuted"] += 1
            continue
        app_path = path[:-1]
        node = _bare(subterm_at(o.term, app_path))
        if not isinstance(node, LamApp):
            stats["refuted"] += 1
            continue
        # WHICH SIDE p SITS ON IS NOT A CONDITION — it SELECTS THE OPTION.
        # p on tb means the reader stands at the B side, so the next step is
        # option 2; p on ta means they stand at A, so it is option 1. Both are
        # legitimate positions, and which one a firing leaves depends on where
        # the question's body uses its entity: `(\c.(l (c d)))` puts it in
        # FUNCTION position, so the pointer lands on ta.
        #
        # Requiring tb here would refuse exactly the questions whose bodies apply
        # their own entity — the common shape — so the side is recorded, not
        # tested.
        ta_path, tb_path = app_path + (0,), app_path + (1,)
        ta_t = type_at(o.term, ta_path)
        tb_t = type_at(o.term, tb_path)
        # [ta] == A and [tb] == B, for BOTH options (§8.6, §4.1).
        if (ta_t, tb_t) != (a_iri, b_iri):
            stats["refuted"] += 1
            continue

        # In option 1 the pointer designates ta (the abstraction position); in
        # option 2 it designates tb, while the RULE 1 REDEX IS STILL AT ta. So
        # applicability is tested at ta either way — option 1 is just the case
        # where pointer and redex coincide (§8.6 note on option 2).
        fires = rule1_applicable(o.term, ta_path)

        if not fires:
            # SILENT: the ontology proposed nothing here, so it is KEPT (not
            # contradicted) and its pointer is ADVANCED to the new application,
            # exactly as the reading advanced actPtr — in BOTH options (§8.6).
            # `path` already designates that application: the reading replaced
            # the subterm there by app(t1,t2), so the position is unchanged and
            # only what sits at it grew.
            # SILENT: the ontology proposed nothing here, so it is KEPT (not
            # contradicted) and the pointer moves to the APPLICATION itself —
            # ont(G(t), (P - {p}) union {pointer to app(ta,tb)}).
            ptrs = dict(o.pointers)
            ptrs[pid] = app_path
            out.append(Ontology(term=o.term, pointers=ptrs, fired=o.fired,
                                is_reading=o.is_reading, origin=o.origin))
            stats["silent"] += 1
            continue

        if option == 1 and operand_ontologies:
            # A CLOSED READING operand: pair each current ontology with each of
            # the operand's own (§8.6). This is the cartesian product, and the
            # one place the set can grow multiplicatively (RN4).
            for oo in operand_ontologies:
                grafted = replace_at(o.term, tb_path, oo.term)
                landed = substituted_position(grafted, app_path)
                reduced = apply_rule1(grafted, app_path)
                if reduced is None:
                    continue
                ptrs = dict(o.pointers)
                ptrs[pid] = landed if landed is not None else app_path
                # FIRST firing: remember the graph as it was PROPOSED — here
                # the grafted term, i.e. what actually went into the reduction.
                out.append(Ontology(term=reduced, pointers=ptrs,
                                    fired=o.fired + 1,
                                    is_reading=o.is_reading,
                                    origin=o.origin or grafted))
                stats["grafted"] += 1
            continue

        landed = substituted_position(o.term, app_path)
        reduced = apply_rule1(o.term, app_path)
        if reduced is None:
            stats["refuted"] += 1
            continue
        ptrs = dict(o.pointers)
        # THE POINTER FOLLOWS THE SUBSTITUTION. The reading's own actPtr goes to
        # tb — the B side of the step — and tb's counterpart here is the B-typed
        # material the substitution just put in place, not the root of the
        # reduct. Rule 1 is linear, so that position is unique.
        #
        # Landing on the reduct's root instead strands the pointer: the next
        # step's redex sits wherever the question's body left it, and §8.6 tests
        # only AT the pointer, so the candidate would be refuted for its depth
        # rather than for its content.
        ptrs[pid] = landed if landed is not None else path
        # FIRST firing: keep the pre-reduction graph (§8.1 `origin`).
        out.append(Ontology(term=reduced, pointers=ptrs, fired=o.fired + 1,
                            is_reading=o.is_reading,
                            origin=o.origin or o.term))
        stats["fired"] += 1

    return (out, stats)


def _sharing_fan_above(term: LamTerm, path: Path) -> Optional[Path]:
    """The path of the SHARING FAN-IN standing above `path`, or None.

    §8.7's second branch asks whether the sharing is ALREADY in place above the
    pointed subterm — the fan is toc's PARENT, toc being what it shares. So this
    walks up from `path`, not at it.

    THERE IS ONLY ONE KIND OF SHARING (§1): the middle-wire fan-in. A reused
    entity is a FREE VARIABLE occurring in both sides of an application, and GAL
    shares it by exactly that fan — GAL page 7, first picture. `_Compiler._fan_in`
    builds it, marked on OFFSET, which is what distinguishes it from the syntactic
    lambda/@ fans on the folded command wire (§8.5(2)).

    IN THE TERM REPRESENTATION that one node shows up two ways, and BOTH count:

      an explicit `LamFan`   when there are casts to record (a reflection);
      a node reached twice   when there are not (entity reuse) — the fan is
                             implicit, and compiling the term materialises it.

    Testing only for `LamFan` would be testing the representation instead of the
    graph: it would reject the (ab)a reading of §1, whose `a` IS shared by such a
    fan, and would refute every candidate at every reflection.

    Returns the path of the enclosing node that does the sharing.
    """
    if not path:
        return None
    parent = path[:-1]
    if isinstance(subterm_at(term, parent), LamFan):
        return parent                       # explicit: a reflection's fan

    # implicit: the pointed node is reached from more than one place, so a
    # sharing fan-in stands above it. Report the enclosing node as that fan.
    node = subterm_at(term, path)
    if _occurrences(term, node) > 1:
        return parent
    return None


def _occurrences(term: LamTerm, node: LamTerm) -> int:
    """How many times `node` is reached in `term`, BY IDENTITY.

    Identity, not equality: sharing in a GAL graph is one node reached twice, and
    two structurally equal but distinct subterms are two nodes (§1).
    """
    n = 0
    if term is node:
        n += 1
    if isinstance(term, LamApp):
        n += _occurrences(term.func, node) + _occurrences(term.arg, node)
    elif isinstance(term, LamAbs):
        n += _occurrences(term.body, node)
    elif isinstance(term, LamFan):
        n += (_occurrences(term.principal, node)
              + _occurrences(term.grey_ctx, node)
              + _occurrences(term.black_ctx, node))
    return n


def _occurs(hay: LamTerm, needle: LamTerm) -> bool:
    """Is `needle` present in `hay` AS THE SAME GRAPH NODE?

    Identity, not equality: §8.7 asks whether G(toc) is ALREADY SHARED in both
    branches, and sharing in a GAL graph is one node reached twice. Two
    structurally equal but distinct subterms are two nodes, i.e. NOT shared.
    """
    if hay is needle:
        return True
    if isinstance(hay, LamApp):
        return _occurs(hay.func, needle) or _occurs(hay.arg, needle)
    if isinstance(hay, LamAbs):
        return _occurs(hay.body, needle)
    if isinstance(hay, LamFan):
        return (_occurs(hay.principal, needle)
                or _occurs(hay.grey_ctx, needle)
                or _occurs(hay.black_ctx, needle))
    return False


def _shared_app_ab(term: LamTerm, toc: LamTerm,
                   a_iri: str, b_iri: str, type_at) -> bool:
    """§8.7's OUTER TEST, common to both branches (§8.8: the strong gate).

    Does `term` contain a subtree app(ta, tb) with [ta] == A, [tb] == B, and
    G(toc) ALREADY SHARED as a subterm of BOTH ta and tb?

    Two separate requirements, per §8.7: the reducer's own redex condition never
    implies the already-shared one — rule 4 CREATES duplication rather than
    demanding it — so this filter is layered on top and computed here.
    """
    def walk(t: LamTerm, path: Path) -> bool:
        node = _bare(t)
        if isinstance(node, LamApp):
            ta, tb = node.func, node.arg
            if (type_at_term(ta, a_iri) and type_at_term(tb, b_iri)
                    and _occurs(ta, toc) and _occurs(tb, toc)):
                return True
        if isinstance(node, LamApp):
            return walk(node.func, path + (0,)) or walk(node.arg, path + (1,))
        if isinstance(node, LamAbs):
            return walk(node.body, path + (0,))
        return False

    def type_at_term(sub: LamTerm, want: str) -> bool:
        try:
            return type_at_of(sub) == want
        except Exception:
            return False

    def type_at_of(sub: LamTerm) -> Optional[str]:
        # `type_at` is given as (term, path); ask it about `sub` as its own root
        return type_at(sub, ())

    return walk(term, ())


# ── update on a reflection  (§8.7) ─────────────────────────────────────────

def after_reflection(ontologies: list[Ontology], pid: int,
                     left_pid: int, right_pid: int,
                     a_iri: str, b_iri: str, c_iri: str,
                     type_at) -> tuple[list[Ontology], dict]:
    """Update the set for a reflection the user just performed (§8.7).

    ONE OUTER TEST, then TWO BRANCHES. The outer test is §8.8's strong gate and
    applies to both: [toc] == C, and t must ALREADY contain app(ta, tb) with
    [ta] == A, [tb] == B and G(toc) shared as a subterm of BOTH ta and tb.
    Passing it, the branches differ only in whether anything reduces:

      toc IS an abstraction      -> RULE 4 fires; the new pointers land on the two
                                    copies of the abstraction's fan.
      toc is NOT an abstraction,
      but a SHARING FAN-IN is
      already ABOVE it           -> NOTHING reduces; G(t) is unchanged and the new
                                    pointers land on that fan's two top edges.
                                    NO abstraction is needed in this branch.

    An ontology failing the outer test, or passing it with neither branch, is
    REFUTED. So reflection refutes HARDER than contraction, whose non-firing
    branch demands no structure of its own.
    """
    out: list[Ontology] = []
    stats = {"fired": 0, "silent": 0, "refuted": 0}

    for o in ontologies:
        path = o.corresponding(pid)
        if path is None:
            stats["refuted"] += 1
            continue
        raw = subterm_at(o.term, path)
        toc = _bare(raw)

        # THE OUTER TEST, required by BOTH branches (§8.7, §8.8): the ontology
        # must ALREADY contain app(ta,tb) with [ta]==A, [tb]==B and G(toc) shared
        # in both. [toc] == C is checked here too.
        if type_at(o.term, path) != c_iri:
            stats["refuted"] += 1
            continue
        if not _shared_app_ab(o.term, raw, a_iri, b_iri, type_at):
            stats["refuted"] += 1
            continue

        if isinstance(toc, LamAbs):
            # RULE 4 fires: the abstraction is duplicated onto the two branches.
            # Both new pointers designate copies of the ABSTRACTION's fan (§8.5).
            fan = LamFan(principal=toc, grey_cast=a_iri, black_cast=b_iri)
            new_term = replace_at(o.term, path, fan)
            ptrs = {k: v for k, v in o.pointers.items() if k != pid}
            ptrs[left_pid] = path + (0,)
            ptrs[right_pid] = path + (1,)
            out.append(Ontology(term=new_term, pointers=ptrs, fired=o.fired + 1,
                                is_reading=o.is_reading,
                                origin=o.origin or o.term))
            stats["fired"] += 1
            continue

        # toc is NOT an abstraction. §8.7's second branch: NO abstraction is
        # needed if a SHARING FAN-IN is ALREADY ABOVE toc. The fan is the PARENT
        # — toc is what it shares — so look up the path, not at it.
        fan_path = _sharing_fan_above(o.term, path)
        if fan_path is not None:
            # NOTHING REDUCES. The pointers advance onto that fan's two top edges.
            ptrs = {k: v for k, v in o.pointers.items() if k != pid}
            ptrs[left_pid] = fan_path + (0,)
            ptrs[right_pid] = fan_path + (1,)
            out.append(Ontology(term=o.term, pointers=ptrs, fired=o.fired,
                                is_reading=o.is_reading, origin=o.origin))
            stats["silent"] += 1
            continue

        stats["refuted"] += 1

    return (out, stats)


# ── the cap  (O7) ──────────────────────────────────────────────────────────

def has_unfired_abstraction(term: LamTerm) -> bool:
    """Does `term` still contain an abstraction — something that COULD yet fire?

    An ontology with no abstraction left is SPENT: rule 1 fires where an
    abstraction meets an application, so such a candidate can never fire again,
    however the reading continues. One that still holds an abstraction can.
    """
    t = _bare(term)
    if isinstance(t, LamAbs):
        return True
    if isinstance(t, LamApp):
        return has_unfired_abstraction(t.func) or has_unfired_abstraction(t.arg)
    return False


def cap(ontologies: list[Ontology],
        limit: Optional[int] = None) -> tuple[list[Ontology], int]:
    """Return the set UNCHANGED. Nothing is evicted.

    Kept as a function so the update rules read the same and a bound can be
    reinstated in one place if O7 ever demands it. `limit=None` (the default,
    from MAX_ONTOLOGIES) means no bound; an explicit integer still truncates,
    which the tests use to exercise the ordering.

    WHY NOTHING IS EVICTED. §8.8 defines the only way out of the set: an
    ontology is DISCARDED BY NEVER BEING ADDED, i.e. by failing an update rule's
    conditions. Eviction is a different thing — dropping a model that the rules
    kept — and it cost real models: see the note on MAX_ONTOLOGIES.

    The eviction ORDER is still defined here (live before spent, then `fired`),
    because it is the right order if a bound is ever wanted; it simply is not
    applied by default.
    """
    if limit is None or len(ontologies) <= limit:
        return (ontologies, 0)
    mirror = [o for o in ontologies if o.is_reading]
    rest = [o for o in ontologies if not o.is_reading]
    room = max(limit - len(mirror), 0)
    ranked = sorted(rest,
                    key=lambda o: (not has_unfired_abstraction(o.term), -o.fired))
    kept = mirror + ranked[:room]
    return (kept, len(ontologies) - len(kept))
