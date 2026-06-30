"""Optimal lambda reduction via sharing graphs — the GAL *bus* algorithm.

Faithful implementation of the bus-of-wires formulation of Gonthier, Abadi &
Lévy, *The Geometry of Optimal Lambda Reduction* (POPL 1992) — "paper1",
`/home/marek/uochb/work/as/geometryOfOptimalLambdaReductionPDFA.pdf`.

The bus formulation (paper §3.3-3.4, Figure 2) replaces Lamping's integer
*indices* with explicit *buses*: every edge is a bundle of `width` parallel
wires, and a fan/bracket/croissant acts on an explicit physical wire SLOT
rather than at an abstract index. There are exactly six reduction-rule schemas
(Figure 2); width may grow (rules 2, 5) or shrink (rule 6) during reduction.

Nodes (paper §3.1, bus forms §3.3):
  ROOT       width-3 interface (result + free variables). Slots BASE/OFFSET/
             COMMAND. Initially every root is arity 3.
  VOID       a plug of arbitrary width (paper ⊗).
  FAN        principal / grey / black, all of equal width n; `main` is the
             physical slot at which the grey (∘) or black (⋆) mark is placed.
             (Bus fan of arity 3n; paper p6 "new fan".)
  BRACKET    narrow (n) <-> wide (n+1); combines two adjacent wires at `slot`
             into one. (Bus bracket = ternary node, paper p5.)
  CROISSANT  wide (n) <-> thin (n-1); creates/removes one wire at `slot`
             ex nihilo (context box). (Bus croissant = unary node, paper p5.)

Reduction rules (paper Figure 2; numbering per project notes):
  R1 fan/fan        same wire   -> annihilate (grey-grey, black-black)
  R2 bracket/bracket same slot  -> annihilate on wide side  (width +1)
  R3 croissant/croissant same slot -> annihilate on thin side (width -1)
  R4 fan/fan        diff wires  -> 2x2 grid of four fans
  R5 fan/bracket    diff wires  -> commute; bracket duplicates; width +1
  R6 fan/croissant  diff wires  -> commute; croissant duplicates; width -1

Context semantics (paper §5.1, p8) is the correctness ground truth and is
implemented in `context_semantics.py`-style helpers below for symbolic rule
validation: a fan maps wire-context b -> ∘.b (grey) / ⋆.b (black) at its main
slot; a bracket combines adjacent a,b -> <a,b>; a croissant inserts the empty
context [] at its slot.

Separate from `lambda_term.py`, whose `beta_reduce_sequence` is the test
oracle: readback(normalize(compile_term(t))) must equal it up to alpha/IRI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from lambda_term import LamTerm, LamVar, LamApp, LamAbs


# ---------------------------------------------------------------------------
# Bus geometry constants
# ---------------------------------------------------------------------------

INITIAL_ROOT_WIDTH = 3
BASE = 0       # base address  (shared by all commands of a lexical-scope call)
OFFSET = 1     # call offset
COMMAND = 2    # the actual command, piled on its subcommands


class NodeKind(Enum):
    ROOT = "root"
    VOID = "void"
    FAN = "fan"
    BRACKET = "bracket"
    CROISSANT = "croissant"
    JOINT = "joint"     # degree-2 pass-through (an explicit edge); inert,
                        # transparent to rules and read-back. Used so a variable
                        # occurrence is a real two-sided edge before closing.


class SyntaxRole(Enum):
    """Read-back tag on a fan; does not affect interaction (paper: roles are a
    visual/read-back aid only)."""
    APP = "app"
    LAM = "lam"
    VAR = "var"
    INTERNAL = "internal"


# ---------------------------------------------------------------------------
# Wires, edges, bus ports
# ---------------------------------------------------------------------------
# A bus PORT owns `width` WireEnds. Two WireEnds are joined by an Edge (one per
# physical wire). connect_bus joins two equal-width ports wire-for-wire.

@dataclass
class WireEnd:
    id: int
    owner: int                 # node id that owns this end
    port: str                  # port name on the owner ("principal"/"grey"/...)
    slot: int                  # index within that port
    peer: Optional[int] = None  # id of the WireEnd this is wired to


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[int, Node] = {}
        self.ends: dict[int, WireEnd] = {}
        self.top_root: Optional[int] = None
        self.free_roots: dict[str, int] = {}
        self._next_node = 0
        self._next_end = 0
        # interaction counters (paper2-style beta vs bookkeeping split)
        self.fan_interactions = 0       # R1, R4  (the "real" work / beta)
        self.book_interactions = 0      # R2, R3, R5, R6 (delimiter shuffling)

    # -- ids ----------------------------------------------------------------
    def _nid(self) -> int:
        i = self._next_node
        self._next_node += 1
        return i

    def _eid(self) -> int:
        i = self._next_end
        self._next_end += 1
        return i

    # -- wire end / bus creation -------------------------------------------
    def new_end(self, owner: int, port: str, slot: int) -> WireEnd:
        w = WireEnd(id=self._eid(), owner=owner, port=port, slot=slot)
        self.ends[w.id] = w
        return w

    def new_bus(self, owner: int, port: str, width: int) -> list[int]:
        return [self.new_end(owner, port, i).id for i in range(width)]

    # -- connection ---------------------------------------------------------
    def connect_wire(self, a: int, b: int) -> None:
        assert self.ends[a].peer is None, f"end {a} already wired"
        assert self.ends[b].peer is None, f"end {b} already wired"
        self.ends[a].peer = b
        self.ends[b].peer = a

    def connect_bus(self, A: list[int], B: list[int]) -> None:
        assert len(A) == len(B), f"bus width mismatch {len(A)} vs {len(B)}"
        for a, b in zip(A, B):
            self.connect_wire(a, b)

    def disconnect(self, a: int) -> Optional[int]:
        """Detach wire end `a` from its peer; return the peer id (or None)."""
        p = self.ends[a].peer
        if p is not None:
            self.ends[p].peer = None
            self.ends[a].peer = None
        return p

    def peer(self, a: int) -> Optional[int]:
        return self.ends[a].peer

    # -- nodes --------------------------------------------------------------
    def add(self, node: "Node") -> "Node":
        self.nodes[node.id] = node
        return node

    def remove_node(self, nid: int) -> None:
        node = self.nodes.pop(nid, None)
        if node is None:
            return
        for eid in node.all_ends():
            self.disconnect(eid)
            self.ends.pop(eid, None)

    # -- constructors (mirror pseudocode §3) -------------------------------
    def new_root(self, label: str, iri: Optional[str] = None,
                 width: int = INITIAL_ROOT_WIDTH) -> "Node":
        nid = self._nid()
        n = Node(id=nid, kind=NodeKind.ROOT, label=label, iri=iri)
        n.ports["bus"] = self.new_bus(nid, "bus", width)
        return self.add(n)

    def new_void(self, width: int) -> "Node":
        nid = self._nid()
        n = Node(id=nid, kind=NodeKind.VOID)
        n.ports["bus"] = self.new_bus(nid, "bus", width)
        return self.add(n)

    def new_fan(self, width: int, main: int,
                role: SyntaxRole = SyntaxRole.INTERNAL,
                iri: Optional[str] = None, label: Optional[str] = None) -> "Node":
        assert width >= 1 and 0 <= main < width, (width, main)
        nid = self._nid()
        n = Node(id=nid, kind=NodeKind.FAN, main=main, role=role,
                 iri=iri, label=label)
        n.ports["principal"] = self.new_bus(nid, "principal", width)
        n.ports["grey"] = self.new_bus(nid, "grey", width)
        n.ports["black"] = self.new_bus(nid, "black", width)
        return self.add(n)

    def new_bracket(self, narrow_width: int, slot: int) -> "Node":
        # combines two adjacent wires: narrow[slot] <-> wide[slot],wide[slot+1]
        assert narrow_width >= 1 and 0 <= slot < narrow_width, (narrow_width, slot)
        nid = self._nid()
        n = Node(id=nid, kind=NodeKind.BRACKET, slot=slot)
        n.ports["narrow"] = self.new_bus(nid, "narrow", narrow_width)
        n.ports["wide"] = self.new_bus(nid, "wide", narrow_width + 1)
        return self.add(n)

    def new_joint(self, width: int) -> "Node":
        """A degree-2 pass-through: up <-> down, same width. Inert (never a
        redex), transparent to read-back. Represents an explicit edge / a
        variable occurrence before its two sides are closed by roots."""
        nid = self._nid()
        n = Node(id=nid, kind=NodeKind.JOINT)
        n.ports["up"] = self.new_bus(nid, "up", width)
        n.ports["down"] = self.new_bus(nid, "down", width)
        return self.add(n)

    def new_open_edge(self, width: int) -> tuple[list[int], list[int]]:
        """Return (up_bus, down_bus) of a fresh JOINT — the two sides of one
        edge, each a list of WireEnd ids ready to connect."""
        j = self.new_joint(width)
        return list(j.ports["up"]), list(j.ports["down"])

    def new_croissant(self, wide_width: int, slot: int) -> "Node":
        # creates one wire: thin (wide_width-1) <-> wide (wide_width) at slot
        assert wide_width >= 1 and 0 <= slot < wide_width, (wide_width, slot)
        nid = self._nid()
        n = Node(id=nid, kind=NodeKind.CROISSANT, slot=slot)
        n.ports["wide"] = self.new_bus(nid, "wide", wide_width)
        n.ports["thin"] = self.new_bus(nid, "thin", wide_width - 1)
        return self.add(n)

    # -- queries ------------------------------------------------------------
    def node_of_end(self, eid: int) -> "Node":
        return self.nodes[self.ends[eid].owner]

    def external_bus(self, port_ends: list[int]) -> list[int]:
        """The list of PEER ends sitting across `port_ends` (the wires this
        port connects to). Used to splice when a redex is removed."""
        peers = []
        for e in port_ends:
            p = self.peer(e)
            assert p is not None, f"external_bus: end {e} dangling"
            peers.append(p)
        return peers

    def check_integrity(self) -> list[str]:
        problems: list[str] = []
        for w in self.ends.values():
            if w.owner not in self.nodes:
                problems.append(f"end {w.id} owner {w.owner} missing")
            if w.peer is None:
                problems.append(
                    f"end {w.id} ({self.nodes.get(w.owner)} {w.port}[{w.slot}])"
                    f" DANGLING")
            elif self.ends.get(w.peer) is None:
                problems.append(f"end {w.id} peer {w.peer} missing")
            elif self.ends[w.peer].peer != w.id:
                problems.append(f"end {w.id} peer not symmetric")
        for n in self.nodes.values():
            problems.extend(n.check_widths())
        return problems


@dataclass
class Node:
    id: int
    kind: NodeKind
    main: int = 0                 # FAN: marked slot
    slot: int = 0                 # BRACKET/CROISSANT: acted-on slot
    role: SyntaxRole = SyntaxRole.INTERNAL
    iri: Optional[str] = None
    label: Optional[str] = None
    ports: dict[str, list[int]] = field(default_factory=dict)  # name -> end ids

    def all_ends(self) -> list[int]:
        out: list[int] = []
        for ends in self.ports.values():
            out.extend(ends)
        return out

    def width(self, port: str) -> int:
        return len(self.ports[port])

    def check_widths(self) -> list[str]:
        p = self.ports
        if self.kind == NodeKind.FAN:
            n = len(p["principal"])
            if not (len(p["grey"]) == n == len(p["black"])):
                return [f"fan#{self.id} unequal port widths"]
            if not (0 <= self.main < n):
                return [f"fan#{self.id} main {self.main} out of range {n}"]
        elif self.kind == NodeKind.BRACKET:
            if len(p["wide"]) != len(p["narrow"]) + 1:
                return [f"bracket#{self.id} wide != narrow+1"]
            if not (0 <= self.slot < len(p["narrow"])):
                return [f"bracket#{self.id} slot {self.slot} out of range"]
        elif self.kind == NodeKind.CROISSANT:
            if len(p["wide"]) != len(p["thin"]) + 1:
                return [f"croissant#{self.id} wide != thin+1"]
            if not (0 <= self.slot < len(p["wide"])):
                return [f"croissant#{self.id} slot {self.slot} out of range"]
        elif self.kind == NodeKind.JOINT:
            if len(p["up"]) != len(p["down"]):
                return [f"joint#{self.id} up != down width"]
        return []


# ---------------------------------------------------------------------------
# Bus slot arithmetic (pseudocode §4)
# ---------------------------------------------------------------------------

def expand_slot_across_bracket(fan_slot: int, bracket_slot: int) -> int:
    """Fan moving from narrow side to wide side of a bracket. The wire at
    bracket_slot becomes two wires (slot, slot+1)."""
    assert fan_slot != bracket_slot
    return fan_slot if fan_slot < bracket_slot else fan_slot + 1


def compress_slot_across_croissant(fan_slot: int, croissant_slot: int) -> int:
    """Fan moving from wide side to thin side of a croissant. The wire at
    croissant_slot disappears."""
    assert fan_slot != croissant_slot
    return fan_slot if fan_slot < croissant_slot else fan_slot - 1


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
    """The (node, port, slot) that wire-end `end`'s peer belongs to."""
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


def _principal_bus_clean(g: Graph, a: Node, b: Node) -> bool:
    """True iff fans a,b face on a clean shared principal bus: equal width and
    every wire a.principal[i] connects straight to b.principal[i] (no bracket /
    croissant spliced in between)."""
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
            # a's principal[main] connects to b. Where on b?
            if bport != "principal":
                return None                     # not principal-to-principal
            if bslot != b.main:
                return None                     # marked wires not aligned
            # The two fans meet on their marked wires. They may annihilate /
            # duplicate ONLY IF their shared principal bus is CLEAN — every wire
            # connects fan-a directly to fan-b. If any wire carries a bracket /
            # croissant, that bookkeeping commutes first (detected from its own
            # side as R5/R6), so we do not fire the fan-fan rule yet.
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
            return Redex(5, a, b, bslot)
        if b.kind == CR:
            if bport != "wide":
                return None
            if bslot == a.main:
                return None                     # deadlock
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
            return Redex(5, b, a, bslot)        # (fan, bracket)
        return None

    # --- a is a CROISSANT (its wide stem meets b) ----------------------------
    if a.kind == CR:
        if b.kind == CR and bport == "wide":
            return Redex(3, a, b) if a.slot == bslot else None
        if b.kind == F and bport == "principal":
            if bslot == b.main:
                return None
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


# ---------------------------------------------------------------------------
# The six Figure-2 reduction rules
# ---------------------------------------------------------------------------
# Each rule: save the EXTERNAL peers of the non-facing ports, remove the redex
# nodes, then rebuild and splice. Removing a node frees its ends, so we must
# read external peers BEFORE removal.

def _ext(g: Graph, ends: list[int]) -> list[int]:
    return g.external_bus(ends)


def _p(g: Graph, end: int) -> Optional[int]:
    """The external peer of a wire-end (None if open)."""
    return g.peer(end)


def _attach_each(g: Graph, fresh: list[int], peers: list[Optional[int]]) -> None:
    """Wire each fresh end to its saved external peer (skipping None)."""
    assert len(fresh) == len(peers), (len(fresh), len(peers))
    for f, p in zip(fresh, peers):
        if p is not None:
            g.connect_wire(f, p)


def rule1_fan_fan_same(g: Graph, a: Node, b: Node) -> None:
    """R1: fans on the SAME wire annihilate. grey<->grey, black<->black;
    width unchanged. (Paper Fig 2 top-left; the i/i fan rule.)"""
    a_grey, a_black = _ext(g, a.ports["grey"]), _ext(g, a.ports["black"])
    b_grey, b_black = _ext(g, b.ports["grey"]), _ext(g, b.ports["black"])
    g.remove_node(a.id)
    g.remove_node(b.id)
    g.connect_bus(a_grey, b_grey)
    g.connect_bus(a_black, b_black)
    g.fan_interactions += 1


def rule2_bracket_bracket_same(g: Graph, a: Node, b: Node) -> None:
    """R2: brackets on the SAME slot annihilate; the two WIDE sides connect,
    so the bus between them is +1 vs the narrow interaction bus."""
    a_wide = _ext(g, a.ports["wide"])
    b_wide = _ext(g, b.ports["wide"])
    g.remove_node(a.id)
    g.remove_node(b.id)
    g.connect_bus(a_wide, b_wide)
    g.book_interactions += 1


def rule3_croissant_croissant_same(g: Graph, a: Node, b: Node) -> None:
    """R3: croissants on the SAME slot annihilate; the two THIN sides connect,
    so the bus between them is -1 vs the wide interaction bus."""
    a_thin = _ext(g, a.ports["thin"])
    b_thin = _ext(g, b.ports["thin"])
    g.remove_node(a.id)
    g.remove_node(b.id)
    g.connect_bus(a_thin, b_thin)
    g.book_interactions += 1


def rule4_fan_fan_diff(g: Graph, bottom: Node, top: Node) -> None:
    """R4: fans on DIFFERENT wires (bottom.main = i < j = top.main) commute
    into a 2x2 grid of four fans. Widths unchanged.

    bottom is replicated onto top's grey & black branches; top is replicated
    onto bottom's grey & black branches; the four new fans cross-wire so that
    bottomOnTop[p].q connects topOnBottom[q].p (p,q in {grey,black}).
    """
    n = bottom.width("principal")
    i, j = bottom.main, top.main
    # external peers of the four branch buses
    top_grey, top_black = _ext(g, top.ports["grey"]), _ext(g, top.ports["black"])
    bot_grey = _ext(g, bottom.ports["grey"])
    bot_black = _ext(g, bottom.ports["black"])
    g.remove_node(bottom.id)
    g.remove_node(top.id)

    # copies of bottom (main=i) sitting on top's grey/black branches
    bot_on_top_grey = g.new_fan(n, i, role=bottom.role, iri=bottom.iri,
                                label=bottom.label)
    bot_on_top_black = g.new_fan(n, i, role=bottom.role, iri=bottom.iri,
                                 label=bottom.label)
    # copies of top (main=j) sitting on bottom's grey/black branches
    top_on_bot_grey = g.new_fan(n, j, role=top.role, iri=top.iri,
                                label=top.label)
    top_on_bot_black = g.new_fan(n, j, role=top.role, iri=top.iri,
                                 label=top.label)

    # principals take the old external branches
    g.connect_bus(top_grey, bot_on_top_grey.ports["principal"])
    g.connect_bus(top_black, bot_on_top_black.ports["principal"])
    g.connect_bus(bot_grey, top_on_bot_grey.ports["principal"])
    g.connect_bus(bot_black, top_on_bot_black.ports["principal"])

    # 2x2 colour grid: bottomOnTop[p].q  <->  topOnBottom[q].p
    g.connect_bus(bot_on_top_grey.ports["grey"], top_on_bot_grey.ports["grey"])
    g.connect_bus(bot_on_top_grey.ports["black"], top_on_bot_black.ports["grey"])
    g.connect_bus(bot_on_top_black.ports["grey"], top_on_bot_grey.ports["black"])
    g.connect_bus(bot_on_top_black.ports["black"], top_on_bot_black.ports["black"])
    g.fan_interactions += 1


def _splice_slot(base: list[int], slot: int, repl: list[int]) -> list[int]:
    """Return `base` with the single entry at `slot` replaced by the list
    `repl` (used to widen/narrow a bus at one position)."""
    return base[:slot] + list(repl) + base[slot + 1:]


def rule5_fan_bracket_diff(g: Graph, fan: Node, bracket: Node, slot: int) -> None:
    """R5 (bus-shared): a bracket sits on the fan's principal bus at `slot`
    (its narrow stem == fan.principal[slot]), slot != fan.main. They commute:
    the fan moves to the bracket's WIDE side (the slot's 1 wire becomes 2, so
    width +1, main shifts by expand_slot_across_bracket); a fresh bracket is
    duplicated onto the fan's grey and black branches at the same slot.
    """
    n = fan.width("principal")
    assert slot != fan.main
    new_main = expand_slot_across_bracket(fan.main, slot)

    # external peers, per wire, of the fan's three ports (slot wires included)
    prin = [_p(g, e) for e in fan.ports["principal"]]
    grey = [_p(g, e) for e in fan.ports["grey"]]
    black = [_p(g, e) for e in fan.ports["black"]]
    br_wide = _ext(g, bracket.ports["wide"])     # 2 wires (the widened slot)
    # the bracket's narrow stem was on fan.principal[slot]; drop that peer.
    g.remove_node(bracket.id)
    g.remove_node(fan.id)

    new_w = n + 1
    nf = g.new_fan(new_w, new_main, role=fan.role, iri=fan.iri, label=fan.label)
    # principal: slot's single wire replaced by the bracket's 2 wide wires.
    new_prin = _splice_slot(prin, slot, br_wide)
    _attach_each(g, nf.ports["principal"], new_prin)

    # grey & black: duplicate the bracket at `slot` (1 wire -> 2 via a bracket)
    g_grey = g.new_bracket(n, slot)              # narrow n / wide n+1
    g_black = g.new_bracket(n, slot)
    _attach_each(g, g_grey.ports["narrow"], grey)
    _attach_each(g, g_black.ports["narrow"], black)
    g.connect_bus(nf.ports["grey"], g_grey.ports["wide"])
    g.connect_bus(nf.ports["black"], g_black.ports["wide"])
    g.book_interactions += 1


def rule6_fan_croissant_diff(g: Graph, fan: Node, croissant: Node,
                             slot: int) -> None:
    """R6 (bus-shared): a croissant sits on the fan's principal bus at `slot`
    (its wide stem == fan.principal[slot]), slot != fan.main. They commute: the
    fan moves to the croissant's THIN side (the slot's wire vanishes, width -1,
    main shifts by compress_slot_across_croissant); a fresh croissant is
    duplicated onto the fan's grey and black branches at the same slot.
    """
    n = fan.width("principal")
    assert n >= 2 and slot != fan.main
    new_main = compress_slot_across_croissant(fan.main, slot)

    prin = [_p(g, e) for e in fan.ports["principal"]]
    grey = [_p(g, e) for e in fan.ports["grey"]]
    black = [_p(g, e) for e in fan.ports["black"]]
    cr_thin = _ext(g, croissant.ports["thin"])   # 0 wires if the croissant is
                                                 # a pure generator (thin width 0)
    g.remove_node(croissant.id)
    g.remove_node(fan.id)

    new_w = n - 1
    nf = g.new_fan(new_w, new_main, role=fan.role, iri=fan.iri, label=fan.label)
    # principal: the slot wire disappears (replaced by the croissant's thin,
    # which has one fewer wire).
    new_prin = _splice_slot(prin, slot, cr_thin)
    _attach_each(g, nf.ports["principal"], new_prin)

    # grey & black: duplicate the croissant at `slot` (1 wire vanishes).
    c_grey = g.new_croissant(n, slot)            # wide n / thin n-1
    c_black = g.new_croissant(n, slot)
    _attach_each(g, c_grey.ports["wide"], grey)
    _attach_each(g, c_black.ports["wide"], black)
    g.connect_bus(nf.ports["grey"], c_grey.ports["thin"])
    g.connect_bus(nf.ports["black"], c_black.ports["thin"])
    g.book_interactions += 1


def reduce_redex(g: Graph, r: Redex) -> None:
    if r.rule == 1:
        rule1_fan_fan_same(g, r.a, r.b)
    elif r.rule == 2:
        rule2_bracket_bracket_same(g, r.a, r.b)
    elif r.rule == 3:
        rule3_croissant_croissant_same(g, r.a, r.b)
    elif r.rule == 4:
        rule4_fan_fan_diff(g, r.a, r.b)
    elif r.rule == 5:
        rule5_fan_bracket_diff(g, r.a, r.b, r.slot)
    elif r.rule == 6:
        rule6_fan_croissant_diff(g, r.a, r.b, r.slot)
    else:
        raise ValueError(f"unknown rule {r.rule}")


# ---------------------------------------------------------------------------
# Context semantics  (paper §5.1, p8) — the correctness ground truth
# ---------------------------------------------------------------------------
# A context is a tree:
#     BOX            the empty context  [] / □   (a node with no descendants)
#     GREY(a)        ∘.a   (Lamping's o; the grey/"call"/left mark)
#     BLACK(a)       ⋆.a   (Lamping's x; the black/"access"/right mark)
#     PAIR(a, b)     <a, b>   (cons / the bracket's combination)
# A bus context is a list of per-wire contexts. The operators act as:
#     fan  at main slot, grey branch:  wire context b -> GREY(b)
#                        black branch:  wire context b -> BLACK(b)
#     bracket at slot:   adjacent wires a,b -> single wire PAIR(a,b)   (combine)
#     croissant at slot: insert a fresh BOX wire                       (create)
# These are exactly fanMark / bracketCombine / croissantInsert of the
# pseudocode (§5), and the six rules must each preserve them (§19).

class Ctx:
    __slots__ = ("tag", "kids")

    def __init__(self, tag: str, kids: tuple = ()):
        self.tag = tag
        self.kids = kids

    def __eq__(self, other) -> bool:
        return (isinstance(other, Ctx) and self.tag == other.tag
                and self.kids == other.kids)

    def __hash__(self):
        return hash((self.tag, self.kids))

    def __repr__(self) -> str:
        if self.tag == "VAR":
            return self.kids[0]
        if self.tag == "BOX":
            return "[]"
        if self.tag == "GREY":
            return f"o.{self.kids[0]}"
        if self.tag == "BLACK":
            return f"x.{self.kids[0]}"
        if self.tag == "PAIR":
            return f"<{self.kids[0]},{self.kids[1]}>"
        return f"{self.tag}{self.kids}"


def BOX() -> Ctx: return Ctx("BOX")
def GREY(a: Ctx) -> Ctx: return Ctx("GREY", (a,))
def BLACK(a: Ctx) -> Ctx: return Ctx("BLACK", (a,))
def PAIR(a: Ctx, b: Ctx) -> Ctx: return Ctx("PAIR", (a, b))
def VAR(name: str) -> Ctx: return Ctx("VAR", (name,))


def symbolic_bus(n: int) -> list[Ctx]:
    return [VAR(f"a{i}") for i in range(n)]


def fan_mark(bus: list[Ctx], main: int, branch: str) -> list[Ctx]:
    out = list(bus)
    out[main] = GREY(out[main]) if branch == "grey" else BLACK(out[main])
    return out


def fan_unmark(bus: list[Ctx], main: int, branch: str) -> list[Ctx]:
    out = list(bus)
    want = "GREY" if branch == "grey" else "BLACK"
    assert out[main].tag == want, (out[main], want)
    out[main] = out[main].kids[0]
    return out


def bracket_combine(bus: list[Ctx], slot: int) -> list[Ctx]:
    # [.. a b ..] -> [.. <a,b> ..]   (narrow has one fewer wire than wide)
    return bus[:slot] + [PAIR(bus[slot], bus[slot + 1])] + bus[slot + 2:]


def bracket_split(bus: list[Ctx], slot: int) -> list[Ctx]:
    assert bus[slot].tag == "PAIR"
    a, b = bus[slot].kids
    return bus[:slot] + [a, b] + bus[slot + 1:]


def croissant_insert(bus: list[Ctx], slot: int) -> list[Ctx]:
    return bus[:slot] + [BOX()] + bus[slot:]


def croissant_remove(bus: list[Ctx], slot: int) -> list[Ctx]:
    assert bus[slot].tag == "BOX"
    return bus[:slot] + bus[slot + 1:]


def validate_bus_rules(max_width: int = 5) -> None:
    """Symbolically verify all six rule schemas preserve context semantics
    (pseudocode §19). Raises AssertionError on any violation."""
    branches = ("grey", "black")
    for n in range(1, max_width + 1):
        # R1 fan/fan same wire: mark then unmark is identity.
        for k in range(n):
            for c in branches:
                ctx = symbolic_bus(n)
                assert fan_unmark(fan_mark(ctx, k, c), k, c) == ctx

        # R2 bracket/bracket same wire: combine then split is identity.
        if n >= 2:
            for k in range(n - 1):
                ctx = symbolic_bus(n)
                assert bracket_split(bracket_combine(ctx, k), k) == ctx

        # R3 croissant/croissant same wire: insert then remove is identity.
        for k in range(n + 1):
            ctx = symbolic_bus(n)
            assert croissant_remove(croissant_insert(ctx, k), k) == ctx

        # R4 fan/fan different wires: marks on distinct wires commute.
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                for c1 in branches:
                    for c2 in branches:
                        ctx = symbolic_bus(n)
                        lhs = fan_mark(fan_mark(ctx, j, c2), i, c1)
                        rhs = fan_mark(fan_mark(ctx, i, c1), j, c2)
                        assert lhs == rhs

        # R5 fan/bracket different wires (matches the graph rule direction:
        # fan moves narrow -> wide, slot expand_slot_across_bracket). On the
        # NARROW side (width n) the bracket is one wire at slot b; a fan-mark
        # on f != b commutes with the bracket's combine. Reading the narrow
        # side as combine(wide), the identity is: marking narrow then "un-
        # combining" (split) equals marking the wide side at the expanded slot.
        for b in range(n):                      # narrow slot; narrow width = n
            narrow = symbolic_bus(n)
            narrow[b] = PAIR(VAR(f"p{b}"), VAR(f"q{b}"))   # the combined wire
            for f in range(n):
                if f == b:
                    continue
                f_wide = expand_slot_across_bracket(f, b)
                for c in branches:
                    lhs = bracket_split(fan_mark(narrow, f, c), b)
                    rhs = fan_mark(bracket_split(narrow, b), f_wide, c)
                    assert lhs == rhs, (n, b, f, c, lhs, rhs)

        # R6 fan/croissant different wires: the croissant created a BOX wire at
        # c_slot on the WIDE (n+1) side; removing it after a fan-mark on some
        # OTHER wire equals fan-marking the THIN (n) bus at the compressed slot.
        for c_slot in range(n + 1):
            thin = symbolic_bus(n)
            wide = croissant_insert(thin, c_slot)   # thin + a BOX at c_slot
            for f in range(n + 1):
                if f == c_slot:
                    continue
                f_thin = compress_slot_across_croissant(f, c_slot)
                for c in branches:
                    lhs = croissant_remove(fan_mark(wide, f, c), c_slot)
                    rhs = fan_mark(thin, f_thin, c)
                    assert lhs == rhs, (n, c_slot, f, c, lhs, rhs)


# ---------------------------------------------------------------------------
# Compile: LamTerm -> bus graph  (paper §4.1, gadgets p7)
# ---------------------------------------------------------------------------
# Every edge is a width-3 bus [BASE, OFFSET, COMMAND] (paper: 3 wires carrying
# the base address, the call offset, and the actual command). A subterm
# fragment exposes:
#   * result : the up-going width-3 bus (the value of the subterm)
#   * free   : map var-iri -> the down-going width-3 bus for that free var
# The COMMAND wire (slot 2) is the "syntax wire": every syntactic fan is marked
# at main = COMMAND and tagged with a SyntaxRole, so read-back can recover the
# term by walking command-wire connectivity (paper §5.2: fans on the rightmost
# wire are the syntax-tree nodes). BASE (0) and OFFSET (1) carry the addressing
# the reduction rules shuffle; they are wired per the p7 diagrams.

Bus = list[int]  # list of WireEnd ids, width 3 = [BASE, OFFSET, COMMAND]


@dataclass
class Fragment:
    result: Bus
    free: dict[str, Bus]
    labels: dict[str, str]


class _Compiler:
    """Graph initialisation per the explicit wiring spec.

    Every subterm fragment exposes width-3 buses [BASE, OFFSET, COMMAND] (the
    "top root" of that fragment). The syntactic fans are WIDTH 2: their ports
    carry [left, right] where the 3 root wires are folded down to 2 by brackets
    (fold: two wires -> one; unfold: one wire -> two). A fan marks the RIGHT
    wire (slot 1, the folded command-bearing wire).
    """

    def __init__(self, g: Graph) -> None:
        self.g = g

    # -- single-wire fold / unfold via a 1-narrow bracket -------------------
    def _fold(self, a: int, b: int) -> int:
        """Combine two wire-ends (a,b) into one via a bracket (a,b are the wide
        side at slots 0,1; the narrow side is the single combined wire)."""
        br = self.g.new_bracket(1, 0)               # narrow 1 / wide 2
        self.g.connect_wire(a, br.ports["wide"][0])
        self.g.connect_wire(b, br.ports["wide"][1])
        return br.ports["narrow"][0]

    def _unfold(self, w: int) -> tuple[int, int]:
        """Split one wire-end into two via a bracket (the inverse orientation):
        w is the narrow side; the two wide wires are returned."""
        br = self.g.new_bracket(1, 0)               # narrow 1 / wide 2
        self.g.connect_wire(w, br.ports["narrow"][0])
        return br.ports["wide"][0], br.ports["wide"][1]

    def _croissant_new(self) -> int:
        """A croissant that starts a new wire ex nihilo; returns the created
        (thin->wide) wire end usable as a fresh OFFSET. The thin side is width
        0 (no input), the wide side is the single new wire."""
        cr = self.g.new_croissant(1, 0)             # wide 1 / thin 0
        return cr.ports["wide"][0]

    # -- variable ----------------------------------------------------------
    def var(self, t: LamVar) -> Fragment:
        # A bare occurrence is a width-3 edge whose two sides are the value
        # (result, up) and the free reference (down).
        result, ref = self.g.new_open_edge(INITIAL_ROOT_WIDTH)
        return Fragment(result=result, free={t.iri: ref},
                        labels={t.iri: t.label})

    # -- application  M N --------------------------------------------------
    def app(self, t: LamApp) -> Fragment:
        """MN gadget (explicit wiring spec). Top fan-IN, all ports width 2.

        top-left  (result):  [ topBASE , fold(topOFFSET, topCOMMAND) ]
        top-right (arg H):   [ H.BASE  , fold(H.OFFSET,  H.COMMAND)  ]
        bottom -> G:  G.BASE   = fan bottom-left
                      G.OFFSET = croissant (new wire)
                      G.COMMAND= fan bottom-right
        """
        g = self.g
        G = self.build(t.func)
        H = self.build(t.arg)

        fan = g.new_fan(2, 1, role=SyntaxRole.APP)   # width 2, main = right wire

        # bottom (principal) -> G : split the 2 wires to G.BASE / G.COMMAND,
        # and create G.OFFSET fresh with a croissant.
        g.connect_wire(fan.ports["principal"][0], G.result[BASE])
        g.connect_wire(self._croissant_new(),     G.result[OFFSET])
        g.connect_wire(fan.ports["principal"][1], G.result[COMMAND])

        # top-left (grey) = result edge (up), width 3 after unfolding
        res_base = fan.ports["grey"][0]
        res_off, res_cmd = self._unfold(fan.ports["grey"][1])
        result = [res_base, res_off, res_cmd]

        # top-right (black) = argument H (up): fold H's OFFSET+COMMAND
        g.connect_wire(fan.ports["black"][0], H.result[BASE])
        g.connect_wire(fan.ports["black"][1],
                       self._fold(H.result[OFFSET], H.result[COMMAND]))

        free, labels = self._merge_free(G, H)
        return Fragment(result=result, free=free, labels=labels)

    # -- abstraction  (lam x).M -------------------------------------------
    def abs(self, t: LamAbs) -> Fragment:
        """(λx).M gadget (explicit wiring spec). Top fan-OUT, all ports width 2.

        top (result):    [ fold(topBASE, topOFFSET) , topCOMMAND ]
        bottom-left -> G:[ G.BASE , fold(G.OFFSET, G.COMMAND) ]
        bottom-right-> x:[ x.BASE , fold(x.OFFSET, x.COMMAND) ]
        each free var y: unfold(G.y.BASE)->(ll,lr); y.BASE=ll;
                         y.OFFSET=fold(lr, G.y.OFFSET); y.COMMAND=G.y.COMMAND
        """
        g = self.g
        body = self.build(t.body)
        bound = body.free.pop(t.var.iri, None)
        body.labels.pop(t.var.iri, None)

        fan = g.new_fan(2, 1, role=SyntaxRole.LAM,
                        iri=t.var.iri, label=t.var.label)

        # top (principal) = result edge (up): fold BASE+OFFSET, keep COMMAND
        res_base, res_off = self._unfold(fan.ports["principal"][0])
        result = [res_base, res_off, fan.ports["principal"][1]]

        # bottom-left (grey) -> G body: G.BASE = left; fold(G.OFFSET,G.COMMAND)=right
        g.connect_wire(fan.ports["grey"][0], body.result[BASE])
        g.connect_wire(fan.ports["grey"][1],
                       self._fold(body.result[OFFSET], body.result[COMMAND]))

        # bottom-right (black) -> bound var x (or plug if unused)
        if bound is None:
            void = g.new_void(2)
            g.connect_bus(fan.ports["black"], void.ports["bus"])
        else:
            g.connect_wire(fan.ports["black"][0], bound[BASE])
            g.connect_wire(fan.ports["black"][1],
                           self._fold(bound[OFFSET], bound[COMMAND]))

        # free vars y crossing the lambda: scope boundary on the BASE wire
        free: dict[str, Bus] = {}
        for iri, ybus in body.free.items():
            ll, lr = self._unfold(ybus[BASE])
            y_base = ll
            y_off = self._fold(lr, ybus[OFFSET])
            free[iri] = [y_base, y_off, ybus[COMMAND]]
        return Fragment(result=result, free=free, labels=body.labels)

    # -- merge shared free variables ---------------------------------------
    def _fan_in(self, a: Bus, b: Bus, iri: str, label: str) -> Bus:
        """Merge two references to the same shared variable via a fan-in. Per
        the init spec the sharing fan-in keeps ARITY 3 (width-3 ports): all
        three wires [BASE, OFFSET, COMMAND] pass straight through on every port.
        It marks the MIDDLE wire (OFFSET, slot 1) — distinct from the syntactic
        λ/@ fans (which act on the folded command wire) and from the BASE-wire
        addressing — so a value flowing into the shared variable duplicates
        (rule 4) rather than annihilating or deadlocking."""
        g = self.g
        fan = g.new_fan(INITIAL_ROOT_WIDTH, OFFSET, role=SyntaxRole.INTERNAL,
                        iri=iri, label=label)
        g.connect_bus(fan.ports["grey"], a)          # use in G (width 3)
        g.connect_bus(fan.ports["black"], b)         # use in H (width 3)
        return list(fan.ports["principal"])          # merged ref (width 3)

    def _merge_free(self, A: Fragment, B: Fragment
                    ) -> tuple[dict[str, Bus], dict[str, str]]:
        free: dict[str, Bus] = {}
        labels: dict[str, str] = {}
        for iri, bus in A.free.items():
            labels[iri] = A.labels.get(iri, iri)
            if iri in B.free:
                free[iri] = self._fan_in(bus, B.free[iri], iri, labels[iri])
            else:
                free[iri] = bus
        for iri, bus in B.free.items():
            if iri not in A.free:
                free[iri] = bus
                labels[iri] = B.labels.get(iri, iri)
        return free, labels

    # -- dispatch ----------------------------------------------------------
    def build(self, t: LamTerm) -> Fragment:
        if isinstance(t, LamVar):
            return self.var(t)
        if isinstance(t, LamApp):
            return self.app(t)
        if isinstance(t, LamAbs):
            return self.abs(t)
        raise TypeError(type(t).__name__)


def compile_term(t: LamTerm) -> Graph:
    """Translate a lambda term into its width-3-bus graph (paper §4.1)."""
    g = Graph()
    frag = _Compiler(g).build(t)

    # re-home the open variable buses' wire-ends onto real owners by closing
    # with roots (the open buses were created with owner -1).
    top = g.new_root("result")
    g.connect_bus(top.ports["bus"], frag.result)
    g.top_root = top.id

    for iri, bus in frag.free.items():
        root = g.new_root(frag.labels.get(iri, iri), iri=iri, width=len(bus))
        g.connect_bus(root.ports["bus"], bus)
        g.free_roots[iri] = root.id

    return g


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
    def __init__(self, g: Graph) -> None:
        self.g = g
        # map LAM fan id -> the LamVar to emit when its bound branch is reached
        self.binder_var: dict[int, LamVar] = {}
        self._depth = 0
        self._max_depth = 5000

    def _walk(self, start_end: int) -> tuple[Node, str]:
        """Follow transparent nodes from `start_end` (a wire we are leaving)
        until the next non-transparent node; return (node, arrival_port)."""
        g = self.g
        e = start_end
        guard = 0
        while True:
            guard += 1
            if guard > 1_000_000:
                raise RuntimeError("read-back: transparent walk did not settle")
            peer = g.peer(e)
            assert peer is not None, "read-back: dangling wire"
            node = g.node_of_end(peer)
            port = g.ends[peer].port
            if node.kind in _TRANSPARENT:
                # exit the opposite port on the SAME slot and continue
                out = _opposite_port(node, port)
                slot = g.ends[peer].slot
                # map slot across width changes: keep the COMMAND wire by using
                # the last wire of each bus (command is the rightmost slot).
                out_ends = node.ports[out]
                e = out_ends[min(slot, len(out_ends) - 1)]
                continue
            return node, port

    def read_branch(self, branch_end0: int) -> LamTerm:
        """Read the subterm reachable from a fan branch (given its slot-0 end;
        we re-walk on the COMMAND wire = last slot of the destination)."""
        self._depth += 1
        if self._depth > self._max_depth:
            raise RuntimeError(
                "read-back: recursion limit — graph likely has a sharing cycle "
                "(incorrect duplication index). See dupI known-failure.")
        try:
            return self._read_branch(branch_end0)
        finally:
            self._depth -= 1

    def _read_branch(self, branch_end0: int) -> LamTerm:
        node, port = self._walk(branch_end0)

        if node.kind == NodeKind.ROOT:
            return LamVar(iri=node.iri or "", label=node.label or "?")

        if node.kind == NodeKind.FAN:
            if node.role == SyntaxRole.LAM and port == "black":
                # reached a lambda via its bound-var branch: an occurrence
                v = self.binder_var.get(node.id)
                if v is None:
                    v = LamVar(iri=node.iri or "", label=node.label or "?")
                return LamVar(iri=v.iri, label=v.label)
            if node.role == SyntaxRole.APP and port == "grey":
                # the application's value comes out the grey branch
                return self.read_app(node)
            if node.role == SyntaxRole.LAM and port == "principal":
                return self.read_lam(node)
            # arrived at a fan-in (shared variable) — transparent: continue
            # through whichever side is not the one we arrived on.
            cont = "principal" if port in ("grey", "black") else "grey"
            return self.read_branch(node.ports[cont][0])

        if node.kind == NodeKind.VOID:
            return LamVar(iri="", label="_")

        raise ValueError(
            f"read-back: unexpected {node.kind.value} "
            f"(role={node.role.value if node.role else None}) via {port}")

    def read_app(self, fan: Node) -> LamApp:
        # function reached down the principal (through call addressing);
        # argument reached up the black branch.
        func = self.read_branch(fan.ports["principal"][0])
        arg = self.read_branch(fan.ports["black"][0])
        return LamApp(func=func, arg=arg)

    def read_lam(self, fan: Node) -> LamAbs:
        var = LamVar(iri=fan.iri or "", label=fan.label or "?")
        self.binder_var[fan.id] = var
        body = self.read_branch(fan.ports["grey"][0])
        return LamAbs(var=var, body=body)


def readback(g: Graph) -> LamTerm:
    assert g.top_root is not None
    rb = _ReadBack(g)
    top = g.nodes[g.top_root]
    return rb.read_branch(top.ports["bus"][0])


# ---------------------------------------------------------------------------
# Normalisation driver
# ---------------------------------------------------------------------------

def normalize(g: Graph, max_steps: int = 1_000_000) -> Graph:
    """Fire active pairs until none remain (or max_steps). Leftmost-first."""
    steps = 0
    while True:
        redexes = find_redexes(g)
        if not redexes:
            return g
        reduce_redex(g, redexes[0])
        steps += 1
        if steps >= max_steps:
            raise RuntimeError(
                f"normalize: exceeded {max_steps} steps "
                f"(fan={g.fan_interactions}, book={g.book_interactions})")


def optimal_normal_form(t: LamTerm) -> LamTerm:
    return readback(normalize(compile_term(t)))
