"""Bus geometry: nodes, wire ends, the Graph container, and slot arithmetic.

The bus-of-wires data model for the GAL optimal-reduction algorithm
(Gonthier-Abadi-Levy, POPL 1992, bus formulation §3.3-3.4). See the package
docstring in ``__init__`` for the full algorithm overview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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
