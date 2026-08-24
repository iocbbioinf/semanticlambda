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

# How many ontologies to keep. Enrichment branches over every abstraction of
# matching type at every pointer on every step, and option 1's closed-reading
# case multiplies by |cr.ontologies| (§8.6 RN4); against that the outer
# structural test refutes heavily (§8.8). Whether the rates balance is O7, so a
# cap is applied and what it drops is reported rather than hidden.
MAX_ONTOLOGIES = 64


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
    origin    THE SHARED GRAPH BEFORE THE FIRST REDUCTION the reading caused —
              the term this ontology was proposed as, prior to any rule firing.
              None while nothing has fired yet, in which case `term` IS the
              origin; `origin_term` resolves that.

              Why it is kept: an ontology is a HYPOTHESIS, and `term` is that
              hypothesis after the reading has consumed part of it. What was
              proposed and what is left are different graphs, and only the
              former shows which abstractions the model actually claimed. It is
              set ONCE, at the first firing, and then carried forward untouched
              — never re-derived, since reduction is not invertible here.
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
                # Enrichment SUBSTITUTES, it does not reduce, so `fired` and the
                # origin are inherited: a candidate grown from an already-reduced
                # ontology still descends from that ontology's proposal.
                cand = Ontology(term=new_term, pointers=ptrs, fired=o.fired,
                                origin=o.origin)
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
                                    fired=o.fired, origin=o.origin)
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
    """
    out: list[Ontology] = []
    for q in abstractions_of_type(a_iri, pool):
        cand = LamApp(q, operand)
        out.append(Ontology(term=cand, pointers={pid: path}))
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
    """
    out: list[Ontology] = []
    for q in abstractions_of_type(a_iri, pool):
        out.append(Ontology(term=LamApp(q, stayed), pointers={pid: path}))
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
        if path is None:
            continue
        node = _bare(subterm_at(o.term, path))
        if not isinstance(node, LamApp):
            continue
        # forward (option 1): the reader stands at ta; tb is the entity offered
        # reverse (option 2): the reader stands at tb; ta is the entity offered
        other = path + (0 if reverse else 1,)
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
        # The reading has just built app(ta, tb) at this position. The ontology
        # must already have that shape, with the right types.
        #
        # [OPEN — O9] This reads the test AGAINST THE ONTOLOGY BEFORE THE STEP,
        # which is what §8.8 states. The alternative reading — that the
        # contraction extends the ontology's term too, so the test applies to the
        # result — would let a bare seed survive its first contraction via the
        # non-firing branch below, and would make a reflection-free reading
        # trivially an ontology of itself. §8 does not settle which is meant; see
        # O9 for the evidence either way. Do not switch without settling it.
        node = _bare(subterm_at(o.term, path))
        if not isinstance(node, LamApp):
            stats["refuted"] += 1
            continue
        ta_path, tb_path = path + (0,), path + (1,)
        ta_t = type_at(o.term, ta_path)
        tb_t = type_at(o.term, tb_path)
        # [ta] == A and [tb] == B, for BOTH options (§8.6).
        #
        # Uniform because BOTH options build app(t1, t2) with [t1] == A and
        # [t2] == B (§4.1); they differ only in WHICH of the two actPtr
        # designates — t1 in option 1 (the reader moves to B), t2 in option 2
        # (the reader stays at B). The letters follow position in the term, so no
        # transposition is needed.
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
            ptrs = dict(o.pointers)
            ptrs[pid] = path
            out.append(Ontology(term=o.term, pointers=ptrs, fired=o.fired,
                                origin=o.origin))
            stats["silent"] += 1
            continue

        if option == 1 and operand_ontologies:
            # A CLOSED READING operand: pair each current ontology with each of
            # the operand's own (§8.6). This is the cartesian product, and the
            # one place the set can grow multiplicatively (RN4).
            for oo in operand_ontologies:
                grafted = replace_at(o.term, tb_path, oo.term)
                reduced = apply_rule1(grafted, path)
                if reduced is None:
                    continue
                ptrs = dict(o.pointers)
                ptrs[pid] = path
                # FIRST firing: remember the graph as it was PROPOSED — here
                # the grafted term, i.e. what actually went into the reduction.
                out.append(Ontology(term=reduced, pointers=ptrs,
                                    fired=o.fired + 1,
                                    origin=o.origin or grafted))
                stats["grafted"] += 1
            continue

        reduced = apply_rule1(o.term, path)
        if reduced is None:
            stats["refuted"] += 1
            continue
        ptrs = dict(o.pointers)
        # option 1: the pointer moves to tb (the reader moved to B).
        # option 2: P is carried over unchanged (the reader did not move), but
        # rebased onto the rewritten term — same position, new identity.
        ptrs[pid] = path
        # FIRST firing: keep the pre-reduction graph (§8.1 `origin`).
        out.append(Ontology(term=reduced, pointers=ptrs, fired=o.fired + 1,
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
                                origin=o.origin))
            stats["silent"] += 1
            continue

        stats["refuted"] += 1

    return (out, stats)


# ── the cap  (O7) ──────────────────────────────────────────────────────────

def cap(ontologies: list[Ontology],
        limit: int = MAX_ONTOLOGIES) -> tuple[list[Ontology], int]:
    """Keep at most `limit` ontologies, preferring the CONFIRMED ones.

    O7 is unsettled — nothing guarantees enrichment's growth and refutation's
    pruning balance — so a bound is applied rather than left to chance. Eviction
    is by `fired` descending, which is O8's proposal: an ontology whose
    hypotheses kept paying off is a better approximation than one that merely
    stayed silent. What is dropped is REPORTED, never hidden.
    """
    if len(ontologies) <= limit:
        return (ontologies, 0)
    ranked = sorted(ontologies, key=lambda o: -o.fired)
    return (ranked[:limit], len(ontologies) - limit)
