"""Active-pair detection: classify bus-shared operator meetings into the six
Figure-2 rule schemas (paper §3.4). See ``rules`` for the rewrites themselves."""

from __future__ import annotations

from typing import Optional

from .graph import Graph, Node, NodeKind


# ---------------------------------------------------------------------------
# Active pairs — BUS-SHARED (per-wire) detection
# ---------------------------------------------------------------------------
# In the bus model an operator sits ON a bus at a particular wire; two operators
# interact when they meet on the SAME bus, with the bus's other wires passing
# straight through (paper §3.4: "we draw only special cases, from which the
# general cases can be deduced by varying the width of buses"; pseudocode §7:
# "the matched region may be embedded in a larger bus").
#
# Each operator has a single PRINCIPAL WIRE — the wire it "points with":
#     FAN        principal[main]      (the marked wire of its principal port)
#     BRACKET    narrow[slot]         (the single combined wire / its stem)
#     CROISSANT  wide[slot]           (the created wire / its stem)
# An operator B meets operator A when B's principal wire is the SAME edge as a
# wire belonging to A. The pair is then classified by which of A's ports/slots
# that wire sits at, relative to A's own principal wire.

def _principal_wire(n: Node) -> int:
    if n.kind == NodeKind.FAN:
        return n.ports["principal"][n.main]
    if n.kind == NodeKind.BRACKET:
        return n.ports["narrow"][n.slot]
    if n.kind == NodeKind.CROISSANT:
        return n.ports["wide"][n.slot]
    raise ValueError(n.kind)


def _locate(g: Graph, end: int) -> Optional[tuple[Node, str, int]]:
    """The (node, port, slot) that wire-end `end`'s peer belongs to. Joints are
    spliced out before reduction, so no through-joint walk is needed."""
    p = g.peer(end)
    if p is None:
        return None
    w = g.ends[p]
    node = g.nodes.get(w.owner)
    if node is None:
        return None
    return node, w.port, w.slot


class Redex:
    """A reducible active pair, classified into one of the six Fig-2 rules.
    `slot` records WHERE on the shared bus the secondary operator sits."""
    __slots__ = ("rule", "a", "b", "slot")

    def __init__(self, rule: int, a: Node, b: Node, slot: int = 0) -> None:
        self.rule = rule
        self.a = a
        self.b = b
        self.slot = slot

    def nodes(self) -> tuple[int, int]:
        return (self.a.id, self.b.id)

    def __repr__(self) -> str:
        return (f"Redex(R{self.rule}, {self.a.kind.value}#{self.a.id}"
                f" <-> {self.b.kind.value}#{self.b.id} @slot {self.slot})")


def _touches_root(g: Graph, ends: list[int]) -> bool:
    """True if any wire of `ends` connects directly to a ROOT node. Used to keep
    the interface delimiters (between a fan and the result/free roots) inert:
    they are part of the compiled normal form, not reducible redexes (a crude
    stand-in for the paper's accessibility / orientation, sufficient here)."""
    for e in ends:
        p = g.peer(e)
        if p is not None and g.nodes[g.ends[p].owner].kind == NodeKind.ROOT:
            return True
    return False


def _principal_bus_clean(g: Graph, a: Node, b: Node) -> bool:
    """True iff fans a,b face on a clean shared principal bus: equal width and
    every wire a.principal[i] reaches b.principal (through JOINTs only — no
    bracket/croissant spliced in between)."""
    ea, eb = a.ports["principal"], b.ports["principal"]
    if len(ea) != len(eb):
        return False
    bset = set(eb)
    for e in ea:
        p = g.peer(e)
        if p is None or p not in bset:
            return False
    return True


def _classify_at(g: Graph, a: Node) -> Optional[Redex]:
    """Classify the interaction at operator `a`'s principal wire, if any.

    Looks at the operator on the far end of a's principal wire and decides which
    Fig-2 rule fires, recording the slot at which the operators meet on the
    shared bus. Returns None if inert or deadlocked. (We only need to detect
    from one side; the driver dedups by node pair.)
    """
    F, BR, CR = NodeKind.FAN, NodeKind.BRACKET, NodeKind.CROISSANT
    pw = _principal_wire(a)
    loc = _locate(g, pw)
    if loc is None:
        return None
    b, bport, bslot = loc
    if b.id == a.id:
        return None

    # --- a is a FAN ----------------------------------------------------------
    if a.kind == F:
        # b is whatever sits on a's principal wire.
        if b.kind == F:
            # a's principal[main] lands on b's principal port (somewhere). The
            # two fans interact iff their principal BUSES are cleanly connected
            # (every wire a.principal[i] <-> b.principal[i], no bookkeeping
            # spliced in). Same main -> annihilate (R1); different main ->
            # duplicate (R4). Marked wires need NOT map to each other: on a
            # shared bus a's main wire connects to b's same-index wire, which is
            # b's main only when the mains coincide. If the bus is not clean,
            # the bookkeeping on it commutes first (R5/R6 from its own side).
            if bport != "principal":
                return None
            if not _principal_bus_clean(g, a, b):
                return None
            if a.main == b.main:
                return Redex(1, a, b)
            return (Redex(4, a, b, a.main) if a.main < b.main
                    else Redex(4, b, a, b.main))
        if b.kind == BR:
            # bracket sits on a's principal bus. Its principal wire (narrow)
            # connects to a.principal[bslot]. Commute unless same wire.
            if bport != "narrow":
                return None
            if bslot == a.main:
                return None                     # deadlock (same wire)
            if _touches_root(g, b.ports["wide"]):
                return None                     # interface delimiter (inert)
            return Redex(5, a, b, bslot)
        if b.kind == CR:
            if bport != "wide":
                return None
            if bslot == a.main:
                return None                     # deadlock
            if _touches_root(g, b.ports["thin"]):
                return None                     # interface delimiter (inert)
            return Redex(6, a, b, bslot)
        return None

    # --- a is a BRACKET (its narrow stem meets b) ----------------------------
    if a.kind == BR:
        if b.kind == BR and bport == "narrow":
            # bracket meets bracket stem-to-stem; same slot -> annihilate
            return Redex(2, a, b) if a.slot == bslot else None
        if b.kind == F and bport == "principal":
            # bracket sits on a FAN's principal bus at slot bslot. Commute
            # (R5) unless it is exactly on the fan's marked wire (deadlock).
            if bslot == b.main:
                return None
            if _touches_root(g, a.ports["wide"]):
                return None                     # interface delimiter (inert)
            return Redex(5, b, a, bslot)        # (fan, bracket)
        return None

    # --- a is a CROISSANT (its wide stem meets b) ----------------------------
    if a.kind == CR:
        if b.kind == CR and bport == "wide":
            return Redex(3, a, b) if a.slot == bslot else None
        if b.kind == F and bport == "principal":
            if bslot == b.main:
                return None
            if _touches_root(g, a.ports["thin"]):
                return None                     # interface delimiter (inert)
            return Redex(6, b, a, bslot)        # (fan, croissant)
        return None

    return None


def find_redexes(g: Graph) -> list[Redex]:
    """All reducible active pairs (bus-shared), unordered, deduped by pair."""
    out: list[Redex] = []
    seen: set[frozenset[int]] = set()
    for n in list(g.nodes.values()):
        if n.kind not in (NodeKind.FAN, NodeKind.BRACKET, NodeKind.CROISSANT):
            continue
        r = _classify_at(g, n)
        if r is None:
            continue
        key = frozenset((r.a.id, r.b.id))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out

