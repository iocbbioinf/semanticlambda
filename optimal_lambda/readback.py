"""Read a normalized bus graph back into a LamTerm AST (paper §5.2)."""

from __future__ import annotations

from typing import Optional

from .term import LamTerm, LamVar, LamApp, LamAbs
from .graph import Graph, Node, NodeKind, SyntaxRole


# ---------------------------------------------------------------------------
# Read-back: bus graph -> LamTerm  (paper §5.2)
# ---------------------------------------------------------------------------
# Read-back walks the syntax skeleton carried on the COMMAND wire. Syntactic
# fans (roles APP / LAM) are the tree nodes; bracket / croissant / joint /
# fan-in are transparent. We walk a fan branch transparently until reaching the
# next syntactic fan (via its principal) or a root (free variable). A LAM fan's
# black (bound-var) branch is the binder: reaching it identifies an occurrence
# of that lambda's variable.

_TRANSPARENT = {NodeKind.JOINT, NodeKind.BRACKET, NodeKind.CROISSANT}


def _opposite_port(node: Node, port: str) -> str:
    """The pass-through partner port of a transparent (degree-2-ish) node."""
    if node.kind == NodeKind.JOINT:
        return "down" if port == "up" else "up"
    if node.kind == NodeKind.BRACKET:
        return "wide" if port == "narrow" else "narrow"
    if node.kind == NodeKind.CROISSANT:
        return "thin" if port == "wide" else "wide"
    raise ValueError(node.kind)


class _ReadBack:
    """Read-back by context-semantics token transport (GAL §5.1/5.2).

    Transport is the permissive structural walk that already worked for every
    non-deeply-shared term: follow the COMMAND wire (rightmost slot) through
    transparent bookkeeping (joints/brackets/croissants), branch at the
    syntactic λ/@ fans.  The ONLY thing the old walk got wrong was *sharing
    fan-ins* (INTERNAL fans), which it crossed as a fixed transparent
    pass-through — for deep sharing (Church mult/exp) that loops, because one
    shared body is entered from several occurrences and the walk can't tell
    which way to leave.

    The fix: carry a per-sharing-fan routing STACK of GREY/BLACK marks.  When
    the token enters a fan-in through grey/black (coming from an occurrence) it
    PUSHES that mark for the fan and continues out the principal into the shared
    body.  When it later re-enters that same fan-in through its principal it
    POPS the mark to pick the matching occurrence's branch.  This unfolds a
    body shared by N occurrences into N copies instead of cycling.  The stack
    is keyed by sharing level (the fan's `main`) so independent sharings don't
    interfere; equal-level fan-ins on one path nest correctly LIFO, exactly the
    GAL context discipline restricted to the marks read-back needs."""

    def __init__(self, g: Graph) -> None:
        self.g = g
        # map LAM fan id -> the LamVar to emit when its bound branch is reached
        self.binder_var: dict[int, LamVar] = {}
        self._guard = 0
        self._max_visits = 5_000_000

    def _walk(self, start_end: int) -> tuple[Node, str]:
        """Follow transparent bookkeeping from `start_end` until the next
        fan/root/void; return (node, arrival_port)."""
        g = self.g
        e = start_end
        while True:
            self._guard += 1
            if self._guard > self._max_visits:
                raise RuntimeError("read-back: transparent walk did not settle")
            peer = g.peer(e)
            assert peer is not None, "read-back: dangling wire"
            node = g.node_of_end(peer)
            port = g.ends[peer].port
            if node.kind in _TRANSPARENT:
                out = _opposite_port(node, port)
                slot = g.ends[peer].slot
                out_ends = node.ports[out]
                e = out_ends[min(slot, len(out_ends) - 1)]
                continue
            return node, port

    def read_branch(self, branch_end0: int, stacks: dict[int, list[str]]
                    ) -> LamTerm:
        """Read the subterm reachable from `branch_end0`.  `stacks` maps a
        sharing fan-in's level (its `main`) to the LIFO list of GREY/BLACK
        marks pending for fan-ins at that level on the current path."""
        node, port = self._walk(branch_end0)

        if node.kind == NodeKind.ROOT:
            return LamVar(iri=node.iri or "", label=node.label or "?")

        if node.kind == NodeKind.VOID:
            return LamVar(iri="", label="_")

        if node.kind == NodeKind.FAN:
            role = node.role
            if role == SyntaxRole.LAM and port == "black":
                v = self.binder_var.get(node.id)
                if v is None:
                    v = LamVar(iri=node.iri or "", label=node.label or "?")
                return LamVar(iri=v.iri, label=v.label)
            if role == SyntaxRole.APP and port == "grey":
                return self.read_app(node, stacks)
            if role == SyntaxRole.LAM and port == "principal":
                return self.read_lam(node, stacks)
            # sharing fan-in (INTERNAL): route by the per-level mark stack.
            return self._read_fanin(node, port, stacks)

        raise ValueError(
            f"read-back: unexpected {node.kind.value} "
            f"(role={node.role.value if node.role else None}) via {port}")

    def _read_fanin(self, fan: Node, port: str,
                    stacks: dict[int, list[str]]) -> LamTerm:
        lvl = fan.main
        if port in ("grey", "black"):
            # entered from an occurrence: remember which side, descend into the
            # shared body via the principal.
            child = dict(stacks)
            child[lvl] = child.get(lvl, []) + [port]
            return self.read_branch(fan.ports["principal"][0], child)

        # entered via principal: pop the most recent occurrence mark for this
        # level to decide which branch the shared body continues into.
        stk = stacks.get(lvl, [])
        if stk:
            side = stk[-1]
            child = dict(stacks)
            child[lvl] = stk[:-1]
            return self.read_branch(fan.ports[side][0], child)
        # No pending mark (an unshared/leftover fan-in): a single live
        # occupant — follow grey.
        return self.read_branch(fan.ports["grey"][0], stacks)

    def read_app(self, fan: Node, stacks: dict[int, list[str]]) -> LamApp:
        func = self.read_branch(fan.ports["principal"][0], stacks)
        arg = self.read_branch(fan.ports["black"][0], stacks)
        return LamApp(func=func, arg=arg)

    def read_lam(self, fan: Node, stacks: dict[int, list[str]]) -> LamAbs:
        var = LamVar(iri=fan.iri or "", label=fan.label or "?")
        self.binder_var[fan.id] = var
        body = self.read_branch(fan.ports["grey"][0], stacks)
        return LamAbs(var=var, body=body)


def readback(g: Graph) -> LamTerm:
    assert g.top_root is not None
    rb = _ReadBack(g)
    top = g.nodes[g.top_root]
    return rb.read_branch(top.ports["bus"][0], {})

