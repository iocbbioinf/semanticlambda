"""The six Figure-2 reduction rules plus the ``reduce_redex`` dispatcher
(paper Figure 2). Each rule saves external peers, removes the redex nodes, then
rebuilds and splices the result."""

from __future__ import annotations

from typing import Optional

from .graph import (
    Graph, Node, NodeKind, SyntaxRole,
    expand_slot_across_bracket, compress_slot_across_croissant,
)
from .redex import Redex


# ---------------------------------------------------------------------------
# The six Figure-2 reduction rules
# ---------------------------------------------------------------------------
# Each rule: save the EXTERNAL peers of the non-facing ports, remove the redex
# nodes, then rebuild and splice. Removing a node frees its ends, so we must
# read external peers BEFORE removal.

def _ext(g: Graph, ends: list[int]) -> list[int]:
    return g.external_bus(ends)


def _p(g: Graph, end: int) -> Optional[int]:
    """The external peer of a wire-end (None if open). Joints are removed before
    reduction (see _splice_joints), so this is a plain peer lookup."""
    return g.peer(end)


def _attach_each(g: Graph, fresh: list[int], peers: list[Optional[int]]) -> None:
    """Wire each fresh end to its saved external peer (skipping None)."""
    assert len(fresh) == len(peers), (len(fresh), len(peers))
    for f, p in zip(fresh, peers):
        if p is not None:
            g.connect_wire(f, p)


def _annihilate(g: Graph, a: Node, b: Node, ports: list[tuple[str, str]]) -> None:
    """Annihilate two operators, fusing their matching ports wire-for-wire,
    self-loop-safe. `ports` is a list of (a_port, b_port) pairs to splice
    straight through. A wire of a/b that loops back to a/b (rather than leaving)
    is reconnected to whatever the splice would join it to, by union-find over
    the saved external peers."""
    # Collect the desired wire merges as pairs of (node, port, slot) endpoints,
    # captured as raw peers (may reference a/b's own ends for self-loops).
    pairs: list[tuple[int, int]] = []
    for pa, pb in ports:
        ea, eb = a.ports[pa], b.ports[pb]
        assert len(ea) == len(eb)
        for x, y in zip(ea, eb):
            pairs.append((g.peer(x), g.peer(y)))
    own = {a.id, b.id}
    # Build a map from each removed-fan end -> the external it should fuse with,
    # by walking the merge pairs (an end on a/b in one pair connects two
    # externals across the annihilation).
    # Union-find over endpoint ids; representative prefers a non-(a/b) end.
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for x, y in pairs:
        if x is None or y is None:
            continue
        union(x, y)
    # Also union each operator's INTERNAL self-loops (a wire whose two ends both
    # belong to a/b, e.g. grey[k] <-> black[k] in a duplicated lambda x . x), so
    # the externals on either side of that wire end up in one class.
    for node in (a, b):
        for e in node.all_ends():
            p = g.peer(e)
            if p is not None and g.ends[p].owner in own:
                union(e, p)
    g.remove_node(a.id)
    g.remove_node(b.id)
    # Each union class should contain exactly the external ends to connect; an
    # end belonging to a/b (now removed) is just a relay. Connect the surviving
    # (still-existing) ends in each class pairwise.
    classes: dict[int, list[int]] = {}
    for e in list(parent):
        if e in g.ends:                          # survived (not on a/b)
            classes.setdefault(find(e), []).append(e)
    for members in classes.values():
        for k in range(1, len(members)):
            g.connect_wire(members[0], members[k])


def rule1_fan_fan_same(g: Graph, a: Node, b: Node) -> None:
    """R1: fans on the SAME wire annihilate. grey<->grey, black<->black;
    width unchanged. (Paper Fig 2 top-left; the i/i fan rule.) Self-loop-safe."""
    _annihilate(g, a, b, [("grey", "grey"), ("black", "black")])
    g.fan_interactions += 1


def rule2_bracket_bracket_same(g: Graph, a: Node, b: Node) -> None:
    """R2: brackets on the SAME slot annihilate; the two WIDE sides connect,
    so the bus between them is +1 vs the narrow interaction bus. Loop-safe."""
    _annihilate(g, a, b, [("wide", "wide")])
    g.book_interactions += 1


def rule3_croissant_croissant_same(g: Graph, a: Node, b: Node) -> None:
    """R3: croissants on the SAME slot annihilate; the two THIN sides connect,
    so the bus between them is -1 vs the wide interaction bus. Loop-safe."""
    _annihilate(g, a, b, [("thin", "thin")])
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
    # capture the four old branch externals loop-aware (a self-loop e.g.
    # top.grey[k] <-> top.black[k] in a duplicated lambda x . x must be re-tied
    # between the corresponding NEW principals).
    cap = {}
    for f in (bottom, top):
        cap[f.id] = _capture_ports(g, f, ("grey", "black"))
    g.remove_node(bottom.id)
    g.remove_node(top.id)

    # copies of bottom (main=i) on top's grey/black; copies of top (main=j) on
    # bottom's grey/black.
    bot_on_top_grey = g.new_fan(n, i, role=bottom.role, iri=bottom.iri, label=bottom.label)
    bot_on_top_black = g.new_fan(n, i, role=bottom.role, iri=bottom.iri, label=bottom.label)
    top_on_bot_grey = g.new_fan(n, j, role=top.role, iri=top.iri, label=top.label)
    top_on_bot_black = g.new_fan(n, j, role=top.role, iri=top.iri, label=top.label)

    # The principal of each new fan represents one old branch external:
    #   bot_on_top_grey.principal  <- top.grey      bot_on_top_black <- top.black
    #   top_on_bot_grey.principal  <- bottom.grey   top_on_bot_black <- bottom.black
    new_end = {
        (top.id, "grey"): list(bot_on_top_grey.ports["principal"]),
        (top.id, "black"): list(bot_on_top_black.ports["principal"]),
        (bottom.id, "grey"): list(top_on_bot_grey.ports["principal"]),
        (bottom.id, "black"): list(top_on_bot_black.ports["principal"]),
    }
    # finalize externals + self-loops, keyed by (fan_id, port)
    nd = {f"{fid}:{port}": ends for (fid, port), ends in new_end.items()}
    target = {}
    for (fid, port), ends in new_end.items():
        toks = []
        for tok in cap[fid][port]:
            if tok[0] == "loop":
                toks.append(("loop", f"{fid}:{tok[1]}", tok[2]))
            else:
                toks.append(tok)
        target[f"{fid}:{port}"] = toks
    _finalize(g, nd, target)

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


def _capture_ports(g: Graph, fan: Node, ports: tuple) -> dict:
    """For each named port of `fan`, capture per-wire external peers as tokens:
    ('ext', peer_end) for a wire leaving the fan, or ('loop', port2, slot2) if
    the wire loops back to another wire of THIS fan (e.g. grey[i]<->black[i] in
    lambda x . x). Captured BEFORE the fan is removed."""
    own = fan.id
    out: dict[str, list] = {}
    for port in ports:
        toks: list = []
        for e in fan.ports[port]:
            p = g.peer(e)
            if p is not None and g.ends[p].owner == own:
                w = g.ends[p]
                toks.append(("loop", w.port, w.slot))
            else:
                toks.append(("ext", p))
        out[port] = toks
    return out


def _finalize(g: Graph, new_end: dict, target: dict) -> None:
    """Wire each new-fan end to its captured target. `new_end[port]` lists the
    new-fan-side ends representing OLD wires (one per old wire, except the
    principal which may carry the unfold); `target[port]` the matching tokens
    from _capture_ports. Self-loops are tied between the two new-fan ends.

    Loop resolution uses new_end as the OLD-wire -> new-end map; a ('loop',
    port2, slot2) connects to new_end[port2][slot2]. Each loop is wired once."""
    done: set = set()
    for port, ends in new_end.items():
        tgts = target[port]
        for i, (end, tok) in enumerate(zip(ends, tgts)):
            if end is None:
                continue                         # this new wire vanished
            if tok[0] == "ext":
                if tok[1] is not None:
                    g.connect_wire(end, tok[1])
            else:  # ('loop', port2, slot2)
                _, p2, s2 = tok
                key = frozenset(((port, i), (p2, s2)))
                if key in done:
                    continue
                done.add(key)
                partner = new_end[p2][s2]
                if partner is not None:
                    g.connect_wire(end, partner)


def _fold_pair(g: Graph, a: int, b: int) -> int:
    """Bracket folding two wire-ends (a,b) into one; returns the narrow end.
    (narrow 1 / wide 2; wide[0]=a, wide[1]=b.)"""
    br = g.new_bracket(1, 0)
    g.connect_wire(a, br.ports["wide"][0])
    g.connect_wire(b, br.ports["wide"][1])
    return br.ports["narrow"][0]


def _unfold_one(g: Graph, w: int) -> tuple[int, int]:
    """Bracket unfolding one wire-end into two; returns (wide0, wide1).
    (narrow 1 / wide 2; narrow[0]=w.)"""
    br = g.new_bracket(1, 0)
    g.connect_wire(w, br.ports["narrow"][0])
    return br.ports["wide"][0], br.ports["wide"][1]


def rule5_fan_bracket_diff(g: Graph, fan: Node, bracket: Node, slot: int) -> None:
    """R5 (bus-shared, per the explicit spec). A fold-bracket (narrow 1 / wide 2)
    sits on the fan's principal at `slot` (its narrow stem == principal[slot]),
    slot != main. Commute:

      left (fan width n; principal[slot] is the FOLDED wire)
        ->
      right (fan width n+1; principal[slot] UNFOLDED into [slot, slot+1];
             each bottom branch gets a fold-bracket re-folding [slot, slot+1])

    main shifts by expand_slot_across_bracket. Wires other than the slot pass
    straight through; only the slot wire unfolds (principal) / re-folds (each
    branch).
    """
    n = fan.width("principal")
    assert slot != fan.main
    new_main = expand_slot_across_bracket(fan.main, slot)

    # capture externals as loop-aware tokens (self-loops back to `fan` recorded
    # as ('loop', port, slot) so they can be re-tied between new-fan wires)
    cap = _capture_ports(g, fan, ("principal", "grey", "black"))
    br_wide = _ext(g, bracket.ports["wide"])     # exactly 2 wires
    assert len(br_wide) == 2, f"R5 expects a fold-bracket, got wide {len(br_wide)}"
    g.remove_node(bracket.id)
    g.remove_node(fan.id)

    nf = g.new_fan(n + 1, new_main, role=fan.role, iri=fan.iri, label=fan.label)

    # new_end[port][i] = the new-fan-side end representing OLD wire i (length n);
    # at `slot` a grey/black branch RE-FOLDS the [slot, slot+1] pair, while the
    # principal UNFOLDS into the bracket's two wide externals.
    new_end: dict[str, list[int]] = {}
    target: dict[str, list] = {}
    for port in ("principal", "grey", "black"):
        np = nf.ports[port]
        ends: list[int] = []
        tgt: list = []
        for i in range(n):
            if i == slot:
                if port == "principal":
                    # unfold: slot's 2 new wires go to the bracket's 2 externals
                    ends.extend([np[slot], np[slot + 1]])
                    tgt.extend([("ext", br_wide[0]), ("ext", br_wide[1])])
                else:
                    ends.append(_fold_pair(g, np[slot], np[slot + 1]))
                    tgt.append(cap[port][i])
            else:
                ends.append(np[i if i < slot else i + 1])
                tgt.append(cap[port][i])
        new_end[port] = ends
        target[port] = tgt
    _finalize(g, new_end, target)
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

    cap = _capture_ports(g, fan, ("principal", "grey", "black"))
    cr_thin = _ext(g, croissant.ports["thin"])   # croissant's thin side (≤1 wire)
    g.remove_node(croissant.id)
    g.remove_node(fan.id)

    nf = g.new_fan(n - 1, new_main, role=fan.role, iri=fan.iri, label=fan.label)

    # new_end[port][i] = the end representing OLD wire i (length n), so loop
    # tokens stay index-aligned across ports. The slot wire is consumed:
    #   principal[slot]: was the croissant stem -> dropped (replaced by the
    #     croissant's thin side, usually nothing); represented by a stub end so
    #     the index space stays length n. Its captured token is the (removed)
    #     croissant, so nothing real attaches.
    #   each branch[slot]: absorbed by a fresh croissant (wide 1 / thin 0).
    # Non-slot wires pass straight to the new (n-1)-wide fan branch.
    new_end: dict[str, list[int]] = {}
    target: dict[str, list] = {}
    for port in ("principal", "grey", "black"):
        np = nf.ports[port]                       # width n-1
        ends: list[int] = []
        tgt: list = []
        for i in range(n):
            if i != slot:
                ends.append(np[i if i < slot else i - 1])
                tgt.append(cap[port][i])
            elif port == "principal":
                # the principal slot wire disappears with the croissant (its
                # stem). For a pure generator (thin empty) the wire simply
                # vanishes: None end, which _finalize skips. (A non-empty thin
                # would route here, but the bus translation only emits pure
                # generators, so that path is unused.)
                ends.append(None)
                tgt.append(("ext", None))
            else:
                ends.append(g.new_croissant(1, 0).ports["wide"][0])   # absorb
                tgt.append(cap[port][i])
        new_end[port] = ends
        target[port] = tgt
    _finalize(g, new_end, target)
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

