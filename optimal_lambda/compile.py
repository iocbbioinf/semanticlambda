"""Compile a LamTerm AST into a bus graph (paper §4.1, gadgets p7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .term import LamTerm, LamVar, LamApp, LamAbs
from .graph import Graph, Node, NodeKind, SyntaxRole, INITIAL_ROOT_WIDTH, BASE, OFFSET, COMMAND


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

