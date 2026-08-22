"""Pointer set for an open reading  —  notes/reading_desc §1, §4.

An open reading is R = (G(t), Pr): a term plus a SET of pointers to positions in
it. This module holds Pr and the two operations that change it:

    contraction (§4.1)  re-points actPtr; |Pr| unchanged
    reflection  (§4.2)  replaces actPtr by TWO pointers on the aux-port edges of
                        a new sharing fan-in; |Pr| grows by one

NO OPERATION REMOVES A POINTER (§1), so Pr grows monotonically and is never
empty.

A pointer designates a position in the term together with the OCCURRENCE it is
reached through, because reflection CASTS a type onto an occurrence: one shared
subterm may read as A through one occurrence and B through the other. The cast is
recorded on the pointer.

  NAVIGATION USES THE SHARED SUBTERM'S OWN TYPE, not the cast (§4.2).
  `cast_type` on a Pointer records what was reflected onto that
  occurrence; the claims panels are built from the entity the pointer stands at,
  which the app tracks separately (`_pointer_nodes`). Keeping the two apart is
  deliberate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from optimal_lambda import LamTerm, LamVar, LamApp, LamAbs, lam_to_dict, lam_from_dict

# A path into a term: () is the whole term, (0,) its function, (1,) its argument.
# Through a LamFan, 0 = grey (left-up) and 1 = black (right-up); both descend to
# the SAME shared subject.
Path = tuple[int, ...]


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
    """A SHARING FAN-IN over one subject — reading_desc §4.2.

    Reflection makes one subterm reachable through TWO occurrences: it "occurs
    twice in the term but exists once in the graph". A plain LamApp(t, t) can
    only express that by accident of object identity, which is lost the moment
    the term is serialised or rebuilt — so the sharing is made STRUCTURAL here.

        principal    the single shared subject, stored ONCE
        grey_ctx     context built above the LEFT-UP occurrence
        black_ctx    context built above the RIGHT-UP occurrence
        grey_cast    entity IRI cast onto the left-up edge  (A)
        black_cast   entity IRI cast onto the right-up edge (B)

    A context is a term containing exactly one HOLE, standing for the subject. It
    starts as the bare HOLE and grows as the user contracts AT that occurrence —
    which is how §7.2's construction works: option 2 at each aux port wraps only
    that occurrence, so the subject ends up in ARGUMENT position of both while
    remaining ONE node.

        reflect      ▽(t)                      both ctx = HOLE
        opt2 @grey   grey_ctx  = app(a, HOLE)
        opt2 @black  black_ctx = app(b, HOLE)
        ==> app(a,t) and app(b,t) over one shared t
    """
    principal: "LamTerm"
    grey_ctx: object = HOLE
    black_ctx: object = HOLE
    grey_cast: Optional[str] = None
    black_cast: Optional[str] = None

    # ── the two occurrences, as terms ─────────────────────────────────────

    def branch(self, which: int) -> "LamTerm":
        """Materialise one occurrence: its context with the subject in the hole.

        Both branches return the SAME principal object in the hole, so identity —
        and therefore sharing — is preserved in whatever is built from this.
        """
        ctx = self.grey_ctx if which == 0 else self.black_ctx
        return _fill_hole(ctx, self.principal)

    @property
    def grey(self) -> "LamTerm":
        return self.branch(0)

    @property
    def black(self) -> "LamTerm":
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

    @staticmethod
    def from_dict(d: dict) -> "LamFan":
        return LamFan(
            principal=lam_from_dict_shared(d["principal"]),
            grey_ctx=lam_from_dict_shared(d.get("grey_ctx", {"type": "hole"})),
            black_ctx=lam_from_dict_shared(d.get("black_ctx", {"type": "hole"})),
            grey_cast=d.get("grey_cast"),
            black_cast=d.get("black_cast"),
        )

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
                      arg=_fill_hole(ctx.arg, subject))
    if isinstance(ctx, LamAbs):
        return LamAbs(var=ctx.var, body=_fill_hole(ctx.body, subject))
    return ctx


def _wrap_hole(ctx, wrap):
    """Apply `wrap` (a HOLE -> term function) at the hole's position in ctx."""
    if ctx is HOLE or isinstance(ctx, _Hole):
        return wrap(HOLE)
    if isinstance(ctx, LamApp):
        if _has_hole(ctx.func):
            return LamApp(func=_wrap_hole(ctx.func, wrap), arg=ctx.arg)
        return LamApp(func=ctx.func, arg=_wrap_hole(ctx.arg, wrap))
    if isinstance(ctx, LamAbs):
        return LamAbs(var=ctx.var, body=_wrap_hole(ctx.body, wrap))
    return ctx


def _has_hole(ctx) -> bool:
    if ctx is HOLE or isinstance(ctx, _Hole):
        return True
    if isinstance(ctx, LamApp):
        return _has_hole(ctx.func) or _has_hole(ctx.arg)
    if isinstance(ctx, LamAbs):
        return _has_hole(ctx.body)
    return False


def lam_to_dict_shared(t) -> dict:
    """Serialise a term that may contain LamFan nodes and branch-context HOLEs."""
    if isinstance(t, _Hole):
        return {"type": "hole"}
    if isinstance(t, LamFan):
        return t.to_dict()
    if isinstance(t, LamApp):
        return {"type": "app",
                "func": lam_to_dict_shared(t.func),
                "arg": lam_to_dict_shared(t.arg)}
    if isinstance(t, LamAbs):
        return {"type": "abs", "var": t.var.to_dict(),
                "body": lam_to_dict_shared(t.body)}
    return lam_to_dict(t)


def lam_from_dict_shared(d: dict, registry: "EntityRegistry | None" = None):
    """Deserialise a term that may contain LamFan nodes.

    ENTITY SHARING IS REBUILT, not merely preserved. A reused entity is written
    once per occurrence (a leaf carries no structure worth de-duplicating on
    disk), so identity has to be restored on load: every occurrence of one IRI is
    resolved through a single registry, giving back ONE node reached from each
    place it was used (§1). Without this a saved reading loses its entity sharing
    and renders as if the user had used distinct entities.

    Compound sharing needs no such reconstruction — it is carried structurally by
    LamFan, which stores its subject once (§4.2).
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
                      arg=lam_from_dict_shared(d["arg"], reg))
    if kind == "abs":
        # The BINDER AND ITS OCCURRENCES MUST BE ONE NODE. Types are entities
        # here, so an abstraction's parameter is the same entity as the
        # occurrences it binds — resolving all of them through the registry keeps
        # `abs.var is <each occurrence>`, which is what makes "does this question
        # share the entity it asks about" answerable at all. Building the binder
        # separately would leave it connected to nothing.
        var = reg.get(d["var"]["iri"], d["var"].get("label", d["var"]["iri"]))
        return LamAbs(var=var, body=lam_from_dict_shared(d["body"], reg))
    if kind == "var":
        return reg.get(d["iri"], d.get("label", d["iri"]))
    return lam_from_dict(d)


def subterm_at(term, path: Path):
    """The subterm at `path`, or None if the path does not exist.

    Entering a LamFan by branch 0/1 materialises THAT occurrence: its own context
    with the one shared subject in the hole. Both branches put the SAME principal
    object in place, so identity — and hence sharing — is preserved.

    A LamAbs is entered by branch 0, reaching its BODY. Readings contain no
    abstractions (I6), but ONTOLOGY terms do (§8.1) and §8.4/§8.7 point inside
    them, so the descent has to be total over the term language, not just over
    the part a reading can build.
    """
    cur = term
    for d in path:
        if isinstance(cur, LamFan):
            cur = cur.branch(0 if d == 0 else 1)
        elif isinstance(cur, LamApp):
            cur = cur.func if d == 0 else cur.arg
        elif isinstance(cur, LamAbs) and d == 0:
            cur = cur.body
        else:
            return None
    return cur


def replace_at(term, path: Path, new):
    """`term` with the subterm at `path` replaced by `new` (functional).

    Replacing AT a fan branch rewrites only THAT occurrence's context — the other
    occurrence and the shared subject are untouched. This is what lets option 2
    re-parent one occurrence into argument position while the subject stays one
    node (§7.2).
    """
    if not path:
        return new
    if isinstance(term, LamFan):
        which = 0 if path[0] == 0 else 1
        if len(path) == 1:
            # replace the whole occurrence: express `new` as a context over the
            # shared subject, so the sharing survives the rewrite.
            ctx = _abstract_subject(new, term.principal)
            if ctx is None:
                # `new` does not contain the subject — the occurrence has been
                # replaced outright and this branch no longer shares.
                ctx = new
            if which == 0:
                return LamFan(term.principal, ctx, term.black_ctx,
                              term.grey_cast, term.black_cast)
            return LamFan(term.principal, term.grey_ctx, ctx,
                          term.grey_cast, term.black_cast)
        # deeper: recurse inside that branch's context
        inner = replace_at(term.branch(which), path[1:], new)
        ctx = _abstract_subject(inner, term.principal)
        if ctx is None:
            ctx = inner
        if which == 0:
            return LamFan(term.principal, ctx, term.black_ctx,
                          term.grey_cast, term.black_cast)
        return LamFan(term.principal, term.grey_ctx, ctx,
                      term.grey_cast, term.black_cast)
    if isinstance(term, LamAbs) and path[0] == 0:
        # descend into the body, keeping the binder. Ontology terms contain
        # abstractions (§8.1) and are rewritten inside them; without this the
        # path would be ignored and the subterm returned unchanged.
        return LamAbs(var=term.var, body=replace_at(term.body, path[1:], new))
    if not isinstance(term, LamApp):
        return term
    d, rest = path[0], path[1:]
    if d == 0:
        return LamApp(func=replace_at(term.func, rest, new), arg=term.arg)
    return LamApp(func=term.func, arg=replace_at(term.arg, rest, new))


def _abstract_subject(t, subject):
    """`t` with the (identical) `subject` replaced by HOLE, or None if absent.

    The inverse of _fill_hole: turns a materialised branch back into a context so
    the fan keeps storing the subject exactly once.
    """
    if t is subject:
        return HOLE
    if isinstance(t, LamApp):
        f = _abstract_subject(t.func, subject)
        a = _abstract_subject(t.arg, subject)
        if f is None and a is None:
            return None
        return LamApp(func=f if f is not None else t.func,
                      arg=a if a is not None else t.arg)
    if isinstance(t, LamAbs):
        b = _abstract_subject(t.body, subject)
        return None if b is None else LamAbs(var=t.var, body=b)
    return None


@dataclass
class Pointer:
    """One live position of an open reading — "where the user stays" (§1).

    path       position in the current term.
    cast_type  IRI cast onto this occurrence by a reflection, else None (§4.2).
    origin     how this pointer came about, for display only:
               "init" | "contract" | "reflect-left" | "reflect-right".
    reflect_id groups the two pointers produced by one reflection, so the panel
               can show them as a pair. None for unreflected pointers.
    """
    pid: int
    path: Path
    cast_type: Optional[str] = None
    origin: str = "init"
    reflect_id: Optional[int] = None

    def is_reflected(self) -> bool:
        return self.cast_type is not None


class EntityRegistry:
    """One LamVar per ENTITY, for the lifetime of one reading.

    A reading's graph is a SHARING graph (§1). An entity the user reuses across
    contraction steps is ONE entity, so it must be ONE node in that graph, reached
    from every place it was used — not a fresh variable each time.

    Reflection shares by an explicit fan (§4.2), because there the sharing is the
    step's whole content: one subject viewed under two casts. Entity reuse is
    different — there is nothing to cast and no fork, only the same entity turning
    up again — so it is shared by IDENTITY: the registry hands back the same
    LamVar object, and the term records one node reached twice.

    Consequences:
      - the renderer draws such an entity ONCE, with a share mark, since
        `term_utils._find_shared` detects sharing by object identity;
      - `lam_to_dict_shared` still writes it out once per occurrence, so identity
        sharing of a LEAF does not survive saving. That is acceptable: a leaf
        carries no structure, and re-reading it under one registry restores the
        sharing. Compound sharing is what needs the fan, and has it.
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
    """Pr, plus which member is actPtr (§1: selected before each step)."""
    pointers: list[Pointer] = field(default_factory=list)
    act_pid: Optional[int] = None
    _next_pid: int = 0
    _next_reflect_id: int = 0

    # ── construction ──────────────────────────────────────────────────────

    @classmethod
    def initial(cls) -> "PointerSet":
        """A reading opens with exactly ONE pointer, at the root (§3)."""
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

    def select(self, pid: int) -> bool:
        """Make `pid` the actPtr. Returns False if there is no such pointer."""
        if self.get(pid) is None:
            return False
        self.act_pid = pid
        return True

    def __len__(self) -> int:
        return len(self.pointers)

    # ── the two operations ────────────────────────────────────────────────

    def after_contraction(self, act: Pointer, option: int) -> None:
        """Re-point actPtr at the new application node (§4.1).

        The term at act.path has been replaced by app(t1, t2) — BOTH options build
        that shape, differing only in which side the operand went (reading_desc
        §4.1). The pointer keeps its PATH: the new application sits exactly where
        the old subterm did, so nothing moves in the tree.

        What DOES change is every OTHER pointer whose path ran THROUGH this
        position: the material there is now one level deeper. The stayed-at
        subterm went under branch 0 in option 1 (it is t1, the function side) and
        under branch 1 in option 2 (it is t2, the argument side). Their paths are
        rebased so they keep designating the same material — invariant I1/I2:
        other pointers are untouched AS POSITIONS, which in a rebuilt term means
        their paths must be fixed up.
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
        """Replace actPtr by two pointers on the fan-in's aux-port edges (§4.2).

        The term at act.path has been replaced by a LamFan over t. The two new
        pointers designate THE TWO OCCURRENCES OF t — the grey (left-up) and
        black (right-up) aux ports, paths +(0,) and +(1,) — not an enclosing
        application. That is the load-bearing detail of §4.2: a later contraction
        at such a pointer acts on the shared occurrence itself.

        A and B are cast onto the two occurrences. Navigation still uses the
        shared subterm's own type (see module docstring).
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
