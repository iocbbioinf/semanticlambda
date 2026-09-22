"""Interaction state — the term, its typing, and the pointer set.

Rewrite of `reading_state.py` for the query-collector spec. An open INTERACTION
is (t, P): a term plus a SET of pointers into it. This module holds P, the term
nodes P points into, and the three operations that change them:

    contraction opt.1 (case 1)  app(ta, td); actPtr MOVES to the answer
    contraction opt.2 (case 2)  app(a, tb);  actPtr STAYS
    reflection        (case 3)  app(tc, tc) over ONE shared tc; |P| grows by one

plus the two that end a point without building anything:

    skip              (case 4)  leave the point; its question type is BOUND later
    resume            (case 5)  same, for every point still open

WHAT IS NEW HERE, against `reading_state.py`
--------------------------------------------
1. THE RETYPE (`LamApp.func_type`). The spec types an application as

       app(ta, tb):  [ta]=A, [tb]=B, [app(ta,tb)]=B,
                     but [ta] IN app(ta,tb) is C, chosen by the user

   "in app(ta,tb)" is the load-bearing phrase: C is ta's type AT THIS ONE PLACE.
   The same ta used as the function of another application keeps its own type
   there. So C cannot live on ta (that would retype every use) and cannot be
   derived from ta (that is what gives A) — it is a property of the APPLICATION,
   and that is where it is stored.

   Reading the interpretation back: "entity A in the context of term ta is
   unclear; concretely there is a point in ta, represented by entity C, that is
   unclear. ta becomes a question (function) represented by C." The retype IS ta
   becoming a question: a term in function position, typed by what it asks about.

2. NO ABSTRACTIONS DURING THE LOOP. An interaction term is applicative —
   `t ::= x | (t t)` plus sharing. Abstractions appear only when the interaction
   is closed and the interaction question is built, `lam E1...lam En.(t)` over
   the lambdaList. `LamAbs` is therefore handled in descent/replacement (a closed
   question is still a term one may point into) but is never built here.

3. NO ONTOLOGY LAYER. Dropped from this app.

THE CAST vs THE RETYPE. Both name "an entity attached to an occurrence", and they
are deliberately distinct. A reflection's cast (grey_cast/black_cast) says how a
SHARED subject reads through one of its two occurrences; the retype says what a
function-position term ASKS at one application. Reflection forks a pointer and
casts; contraction retypes and does not fork.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from optimal_lambda import LamVar, LamAbs, lam_to_dict, lam_from_dict
from optimal_lambda import LamApp as _BaseLamApp

# A path into a term: () is the whole term, (0,) its function, (1,) its argument.
# Through a LamFan, 0 = grey (left-up) and 1 = black (right-up); both descend to
# the SAME shared subject.
Path = tuple[int, ...]


@dataclass
class LamApp(_BaseLamApp):
    """Application `(func arg)`, carrying the function-position RETYPE.

        func_type   IRI of C — what `func` is typed as IN THIS application.
                    None means "not retyped": the type follows from `func`
                    itself, exactly as before.

    Storing it here, rather than on `func`, is what makes the retype local to
    one place. Two applications may share one `func` node and retype it
    differently; neither disturbs the other, and neither disturbs `[func]`.
    """
    func_type: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"type": "app",
             "func": lam_to_dict_shared(self.func),
             "arg": lam_to_dict_shared(self.arg)}
        if self.func_type:
            d["func_type"] = self.func_type
        return d

    @staticmethod
    def from_dict(d: dict) -> "LamApp":
        return LamApp(func=lam_from_dict(d["func"]),
                      arg=lam_from_dict(d["arg"]),
                      func_type=d.get("func_type"))


class _Hole:
    """Marks where the shared subject sits inside a fan branch's context."""
    _INSTANCE = None

    def __new__(cls):
        if cls._INSTANCE is None:
            cls._INSTANCE = super().__new__(cls)
        return cls._INSTANCE

    def __repr__(self) -> str:
        return "▽"

    def __str__(self) -> str:
        return "▽"

    def to_dict(self) -> dict:
        return {"type": "hole"}


HOLE = _Hole()


@dataclass
class LamFan:
    """A SHARING FAN-IN over one subject — case 3, reflection.

    Case 3 builds `app(tc, tc)` where "tc is shared by fan-in": tc occurs twice
    in the term but exists ONCE in the graph. A plain LamApp(t, t) expresses that
    only by accident of object identity, which is lost the moment the term is
    serialised — so the sharing is made STRUCTURAL here.

        principal    the single shared subject, stored ONCE
        grey_ctx     context built above the LEFT occurrence
        black_ctx    context built above the RIGHT occurrence
        grey_cast    entity IRI cast onto the left edge  (the question, A)
        black_cast   entity IRI cast onto the right edge (the answer, B)

    A context is a term containing exactly one HOLE, standing for the subject. It
    starts as the bare HOLE and grows as the user contracts AT that occurrence,
    so a later step wraps only that occurrence while the subject stays one node:

        reflect      ▽(t)                      both ctx = HOLE
        opt2 @grey   grey_ctx  = app(a, HOLE)
        opt2 @black  black_ctx = app(b, HOLE)
        ==> app(a,t) and app(b,t) over one shared t
    """
    principal: "object"
    grey_ctx: object = HOLE
    black_ctx: object = HOLE
    grey_cast: Optional[str] = None
    black_cast: Optional[str] = None

    def branch(self, which: int) -> object:
        """Materialise one occurrence: its context with the subject in the hole.

        Both branches put the SAME principal object in the hole, so identity —
        and therefore sharing — survives into whatever is built from this.
        """
        ctx = self.grey_ctx if which == 0 else self.black_ctx
        return _fill_hole(ctx, self.principal)

    @property
    def grey(self) -> object:
        return self.branch(0)

    @property
    def black(self) -> object:
        return self.branch(1)

    def to_dict(self) -> dict:
        # ONE copy of the subject — this is the point of the node.
        return {
            "type": "fan",
            "principal": lam_to_dict_shared(self.principal),
            "grey_ctx": lam_to_dict_shared(self.grey_ctx),
            "black_ctx": lam_to_dict_shared(self.black_ctx),
            "grey_cast": self.grey_cast,
            "black_cast": self.black_cast,
        }

    def __str__(self) -> str:
        p = str(self.principal)
        p_s = p if isinstance(self.principal, LamVar) else f"({p})"
        g, b = str(self.grey_ctx), str(self.black_ctx)
        if g == "▽" and b == "▽":
            return f"▽{p_s}"
        return f"▽{p_s}[{g} | {b}]"


def _fill_hole(ctx, subject):
    """`ctx` with its HOLE replaced by `subject` (same object, so still shared)."""
    if ctx is HOLE or isinstance(ctx, _Hole):
        return subject
    if isinstance(ctx, LamApp):
        return LamApp(func=_fill_hole(ctx.func, subject),
                      arg=_fill_hole(ctx.arg, subject),
                      func_type=ctx.func_type)
    if isinstance(ctx, _BaseLamApp):
        return _BaseLamApp(func=_fill_hole(ctx.func, subject),
                           arg=_fill_hole(ctx.arg, subject))
    if isinstance(ctx, LamAbs):
        return LamAbs(var=ctx.var, qid=ctx.qid,
                      body=_fill_hole(ctx.body, subject))
    return ctx


def _has_hole(ctx) -> bool:
    if ctx is HOLE or isinstance(ctx, _Hole):
        return True
    if isinstance(ctx, _BaseLamApp):
        return _has_hole(ctx.func) or _has_hole(ctx.arg)
    if isinstance(ctx, LamAbs):
        return _has_hole(ctx.body)
    return False


def _abstract_subject(t, subject):
    """`t` with the (identical) `subject` replaced by HOLE, or None if absent.

    The inverse of _fill_hole: turns a materialised branch back into a context so
    the fan keeps storing the subject exactly once.
    """
    if t is subject:
        return HOLE
    if isinstance(t, _BaseLamApp):
        f = _abstract_subject(t.func, subject)
        a = _abstract_subject(t.arg, subject)
        if f is None and a is None:
            return None
        func = f if f is not None else t.func
        arg = a if a is not None else t.arg
        ft = t.func_type if isinstance(t, LamApp) else None
        return LamApp(func=func, arg=arg, func_type=ft)
    if isinstance(t, LamAbs):
        b = _abstract_subject(t.body, subject)
        return None if b is None else LamAbs(var=t.var, qid=t.qid, body=b)
    return None


# ── typing ────────────────────────────────────────────────────────────────


def type_of(t) -> Optional[str]:
    """[t] — the type IRI of a term.

        [x]            = x.iri
        [app(ta,tb)]   = [tb]          the RIGHTMOST LEAF
        [lam a.t]      = a.iri         the bound variable's type

    `func_type` deliberately does NOT enter here: it types the function AT its
    application, not the application. Both of the spec's cases agree with the
    rule as stated — case 1 gives [app(ta,td)] = [td] = D, case 2 gives
    [app(a,tb)] = [tb] = B — so the result type is derived, never stored.

    Closing an interaction says "[t] is the rightmost leaf", which is this rule
    applied repeatedly: [app] = [arg] walks to the right spine's leaf.
    """
    t = _through_fan(t)
    while isinstance(t, _BaseLamApp):
        if isinstance(_through_fan(t.func), LamAbs):
            return _through_fan(t.func).var.iri
        t = _through_fan(t.arg)
    if isinstance(t, LamVar):
        return t.iri
    if isinstance(t, LamAbs):
        return t.var.iri
    return None


def func_type_at(term, path: Path) -> Optional[str]:
    """[ta] IN app(ta,tb) — the retype C recorded at the application at `path`.

    Returns None when the application was never retyped, in which case the
    function's type is simply `type_of(func)`.
    """
    node = subterm_at(term, path)
    node = _through_fan(node)
    if isinstance(node, LamApp):
        return node.func_type
    return None


def type_in_context(term, path: Path) -> Optional[str]:
    """The type of the subterm at `path` AS READ FROM ITS PLACE.

    This is the spec's distinction made operational. For a term in function
    position whose application carries a retype, the answer is C; everywhere
    else it is the term's own type.
    """
    node = subterm_at(term, path)
    if node is None:
        return None
    if path and path[-1] == 0:
        parent = subterm_at(term, path[:-1])
        parent = _through_fan(parent)
        if isinstance(parent, LamApp) and parent.func_type:
            return parent.func_type
    return type_of(node)


def _through_fan(t):
    """A fan reads as its shared subject for typing purposes."""
    while isinstance(t, LamFan):
        t = t.principal
    return t


# ── serialisation ─────────────────────────────────────────────────────────


def lam_to_dict_shared(t) -> dict:
    """Serialise a term that may contain LamFan nodes, retypes and HOLEs."""
    if isinstance(t, _Hole):
        return {"type": "hole"}
    if isinstance(t, LamFan):
        return t.to_dict()
    if isinstance(t, LamApp):
        return t.to_dict()
    if isinstance(t, _BaseLamApp):
        return {"type": "app",
                "func": lam_to_dict_shared(t.func),
                "arg": lam_to_dict_shared(t.arg)}
    if isinstance(t, LamAbs):
        d = {"type": "abs", "var": t.var.to_dict(),
             "body": lam_to_dict_shared(t.body)}
        if t.qid:
            d["qid"] = t.qid
        return d
    return lam_to_dict(t)


def lam_from_dict_shared(d: dict, registry: "EntityRegistry | None" = None):
    """Deserialise a term that may contain LamFan nodes and retypes.

    ENTITY SHARING IS REBUILT, not merely preserved. A reused entity is written
    once per occurrence (a leaf carries no structure worth de-duplicating on
    disk), so identity has to be restored on load: every occurrence of one IRI is
    resolved through a single registry, giving back ONE node reached from each
    place it was used. Without this a saved interaction loses its entity sharing
    and reads as if the user had used distinct entities.

    Compound sharing needs no such reconstruction — it is carried structurally by
    LamFan, which stores its subject once.
    """
    reg = EntityRegistry() if registry is None else registry
    kind = d.get("type")
    if kind == "hole":
        return HOLE
    if kind == "fan":
        return LamFan(
            principal=lam_from_dict_shared(d["principal"], reg),
            grey_ctx=lam_from_dict_shared(d.get("grey_ctx", {"type": "hole"}), reg),
            black_ctx=lam_from_dict_shared(d.get("black_ctx", {"type": "hole"}), reg),
            grey_cast=d.get("grey_cast"),
            black_cast=d.get("black_cast"),
        )
    if kind == "app":
        return LamApp(func=lam_from_dict_shared(d["func"], reg),
                      arg=lam_from_dict_shared(d["arg"], reg),
                      func_type=d.get("func_type"))
    if kind == "abs":
        # The BINDER AND ITS OCCURRENCES MUST BE ONE NODE, so that the binders of
        # an interaction question actually bind the occurrences in its body.
        var = reg.get(d["var"]["iri"], d["var"].get("label", d["var"]["iri"]))
        return LamAbs(var=var, qid=d.get("qid"),
                      body=lam_from_dict_shared(d["body"], reg))
    if kind == "var":
        return reg.get(d["iri"], d.get("label", d["iri"]))
    return lam_from_dict(d)


# ── navigation ────────────────────────────────────────────────────────────


def subterm_at(term, path: Path):
    """The subterm at `path`, or None if the path does not exist.

    Entering a LamFan by branch 0/1 materialises THAT occurrence: its own context
    with the one shared subject in the hole. Both branches put the SAME principal
    object in place, so identity — and hence sharing — is preserved.
    """
    cur = term
    for d in path:
        if isinstance(cur, LamFan):
            cur = cur.branch(0 if d == 0 else 1)
        elif isinstance(cur, _BaseLamApp):
            cur = cur.func if d == 0 else cur.arg
        elif isinstance(cur, LamAbs) and d == 0:
            cur = cur.body
        else:
            return None
    return cur


def replace_at(term, path: Path, new):
    """`term` with the subterm at `path` replaced by `new` (functional).

    Replacing AT a fan branch rewrites only THAT occurrence's context — the other
    occurrence and the shared subject are untouched. This is what lets a later
    contraction re-parent one occurrence while the subject stays one node.
    """
    if not path:
        return new
    if isinstance(term, LamFan):
        which = 0 if path[0] == 0 else 1
        if len(path) == 1:
            inner = new
        else:
            inner = replace_at(term.branch(which), path[1:], new)
        # Express the result as a context over the shared subject, so the
        # sharing survives the rewrite.
        ctx = _abstract_subject(inner, term.principal)
        if ctx is None:
            # the occurrence was replaced outright; this branch no longer shares
            ctx = inner
        if which == 0:
            return LamFan(term.principal, ctx, term.black_ctx,
                          term.grey_cast, term.black_cast)
        return LamFan(term.principal, term.grey_ctx, ctx,
                      term.grey_cast, term.black_cast)
    if isinstance(term, LamAbs) and path[0] == 0:
        return LamAbs(var=term.var, qid=term.qid,
                      body=replace_at(term.body, path[1:], new))
    if not isinstance(term, _BaseLamApp):
        return term
    d, rest = path[0], path[1:]
    ft = term.func_type if isinstance(term, LamApp) else None
    if d == 0:
        return LamApp(func=replace_at(term.func, rest, new), arg=term.arg,
                      func_type=ft)
    return LamApp(func=term.func, arg=replace_at(term.arg, rest, new),
                  func_type=ft)


# ── pointers ──────────────────────────────────────────────────────────────


@dataclass
class Pointer:
    """One live position of an open interaction — where the user stays.

    path       position in the current term.
    cast_type  IRI cast onto this occurrence by a reflection, else None.
    origin     how this pointer came about, for display only:
               "init" | "contract" | "reflect-left" | "reflect-right".
    reflect_id groups the two pointers produced by one reflection. None
               otherwise.
    skipped    the user skipped this point (case 4). The pointer stays in P —
               a skip settles nothing — but it is no longer offered, and its
               question type is bound into the interaction question at close.
    """
    pid: int
    path: Path
    cast_type: Optional[str] = None
    origin: str = "init"
    reflect_id: Optional[int] = None
    skipped: bool = False

    def is_reflected(self) -> bool:
        return self.cast_type is not None


class EntityRegistry:
    """One LamVar per ENTITY, for the lifetime of one interaction.

    An interaction's graph is a SHARING graph. An entity the user reuses across
    steps is ONE entity, so it must be ONE node in that graph, reached from every
    place it was used — not a fresh variable each time.

    Reflection shares by an explicit fan, because there the sharing is the step's
    whole content: one subject under two casts. Entity reuse is different — there
    is nothing to cast and no fork, only the same entity turning up again — so it
    is shared by IDENTITY: the registry hands back the same LamVar object.
    """

    def __init__(self) -> None:
        self._vars: dict[str, LamVar] = {}

    def get(self, iri: str, label: str) -> LamVar:
        """The variable for `iri` — the SAME object every time."""
        v = self._vars.get(iri)
        if v is None:
            v = LamVar(iri=iri, label=label)
            self._vars[iri] = v
        return v

    def __contains__(self, iri: str) -> bool:
        return iri in self._vars

    def __len__(self) -> int:
        return len(self._vars)


@dataclass
class PointerSet:
    """P, plus which member is actPtr (selected before each step).

    NO OPERATION REMOVES A POINTER except reflection, which replaces one by two.
    A skip does not remove: the point stays open, it is merely not offered again,
    and it is what the interaction question binds over.
    """
    pointers: list[Pointer] = field(default_factory=list)
    act_pid: Optional[int] = None
    _next_pid: int = 0
    _next_reflect_id: int = 0

    @classmethod
    def initial(cls) -> "PointerSet":
        """An interaction opens with exactly ONE pointer, at the root."""
        ps = cls()
        p = ps._mint(path=(), origin="init")
        ps.act_pid = p.pid
        return ps

    def _mint(self, path: Path, origin: str,
              cast_type: Optional[str] = None,
              reflect_id: Optional[int] = None) -> Pointer:
        p = Pointer(pid=self._next_pid, path=path, cast_type=cast_type,
                    origin=origin, reflect_id=reflect_id)
        self._next_pid += 1
        self.pointers.append(p)
        return p

    # ── access ────────────────────────────────────────────────────────────

    def act(self) -> Optional[Pointer]:
        for p in self.pointers:
            if p.pid == self.act_pid:
                return p
        return None

    def get(self, pid: int) -> Optional[Pointer]:
        for p in self.pointers:
            if p.pid == pid:
                return p
        return None

    def open_pointers(self) -> list[Pointer]:
        """Those still offered — every pointer the user has not skipped."""
        return [p for p in self.pointers if not p.skipped]

    def select(self, pid: int) -> bool:
        """Make `pid` the actPtr. Re-selecting un-skips it."""
        p = self.get(pid)
        if p is None:
            return False
        p.skipped = False
        self.act_pid = pid
        return True

    def __len__(self) -> int:
        return len(self.pointers)

    # ── the three operations ──────────────────────────────────────────────

    def after_contraction(self, act: Pointer, option: int) -> None:
        """Re-point actPtr after a contraction.

        The term at act.path has been replaced by app(t1, t2) — both options
        build that shape, differing in which side the operand went:

            case 1   app(ta, td)   the stayed-at term is t1, the FUNCTION
            case 2   app(a,  tb)   the stayed-at term is t2, the ARGUMENT

        The actPtr keeps its PATH: the new application sits exactly where the
        old subterm did, so nothing moves. Which entity the user then STANDS AT
        differs by option and is the caller's business — in case 1 the user
        moves to the answer D, in case 2 he stays at B — but that is a fact
        about entities, not about the path.

        What DOES change is every OTHER pointer whose path ran THROUGH this
        position: that material is now one level deeper, under branch 0 in
        case 1 and branch 1 in case 2. Their paths are rebased so they keep
        designating the same material.
        """
        branch = 0 if option == 1 else 1
        n = len(act.path)
        for p in self.pointers:
            if p.pid == act.pid:
                continue
            if len(p.path) > n and p.path[:n] == act.path:
                p.path = act.path + (branch,) + p.path[n:]
        # a contraction never re-casts: the cast, if any, still describes the
        # occurrence the user is standing at.

    def after_reflection(self, act: Pointer,
                         a_iri: str, b_iri: str) -> tuple[Pointer, Pointer]:
        """Replace actPtr by two pointers on the fan's two occurrences.

        The term at act.path has been replaced by a LamFan over tc, i.e. the
        spec's app(tc, tc) with tc shared. The two new pointers designate THE
        TWO OCCURRENCES of tc — paths +(0,) and +(1,) — so that a later step at
        such a pointer acts on the shared occurrence itself:

            P = P - {actPtr}  ∪  {left, right}

        A (the question) and B (the answer) are cast onto the two occurrences.
        Both occurrences still TYPE as C, the shared subject's own type: the
        cast records what the reflection put on the occurrence, and typing
        reads the subject. That is the spec's "both [tc] in app(tc,tc) = C".
        """
        rid = self._next_reflect_id
        self._next_reflect_id += 1

        # Other pointers below this position now pass THROUGH the fan. Their
        # paths gain one step; either branch reaches the same subject, so the
        # branch index is immaterial and 0 (grey) is used by convention.
        n = len(act.path)
        for p in self.pointers:
            if p.pid == act.pid:
                continue
            if len(p.path) > n and p.path[:n] == act.path:
                p.path = act.path + (0,) + p.path[n:]

        self.pointers = [p for p in self.pointers if p.pid != act.pid]
        left = self._mint(act.path + (0,), "reflect-left", a_iri, rid)
        right = self._mint(act.path + (1,), "reflect-right", b_iri, rid)
        if self.act_pid == act.pid:
            self.act_pid = left.pid
        return (left, right)

    def skip(self, act: Pointer) -> None:
        """Case 4 — leave this point unsettled.

        The pointer is NOT removed: the point is still open, it is simply no
        longer offered. What it stands at is bound into the interaction question
        when the interaction closes.
        """
        act.skipped = True
        if self.act_pid == act.pid:
            nxt = self.open_pointers()
            self.act_pid = nxt[0].pid if nxt else None

    def resume(self) -> list[Pointer]:
        """Case 5 — the user resumes the query; every open point is left.

        Returns the pointers this settled nothing at, in order, so the caller
        can append their types to the lambdaList.
        """
        left = self.open_pointers()
        for p in left:
            p.skipped = True
        self.act_pid = None
        return left
