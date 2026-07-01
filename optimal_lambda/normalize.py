"""Reduction driver: splice joints, fire active pairs to normal form, and the
top-level ``optimal_normal_form`` convenience wrapper."""

from __future__ import annotations

from .term import LamTerm
from .graph import Graph, NodeKind
from .redex import find_redexes
from .rules import reduce_redex
from .compile import compile_term
from .readback import readback


# ---------------------------------------------------------------------------
# Normalisation driver
# ---------------------------------------------------------------------------

def _splice_joints(g: Graph) -> None:
    """Remove every JOINT by wiring its up[i] peer straight to its down[i] peer.
    A JOINT is an identity pass-through (the compile-time variable-edge marker);
    splicing it out lets operators on either side meet on a shared bus without a
    JOINT blocking detection. Read-back already treats JOINTs as transparent, so
    this does not change the read-back result.

    Joints can chain (a joint's peer is another joint's end); we splice one at a
    time, re-reading peers live, until none remain."""
    while True:
        nid = next((n.id for n in g.nodes.values()
                    if n.kind == NodeKind.JOINT), None)
        if nid is None:
            return
        j = g.nodes[nid]
        up, down = list(j.ports["up"]), list(j.ports["down"])
        peers_up = [g.peer(e) for e in up]
        peers_down = [g.peer(e) for e in down]
        for e in up + down:
            g.disconnect(e)
        for pu, pd in zip(peers_up, peers_down):
            if pu is not None and pd is not None:
                g.connect_wire(pu, pd)
            # if exactly one side is open (a dangling variable edge), leave it.
        g.remove_node(nid)


def normalize(g: Graph, max_steps: int = 1_000_000) -> Graph:
    """Fire active pairs until none remain (or max_steps). Leftmost-first."""
    _splice_joints(g)            # remove identity pass-throughs up front
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
