"""Symbolic context semantics (paper §5.1) — the correctness ground truth used
to validate the six rules independently of the graph machinery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .graph import expand_slot_across_bracket, compress_slot_across_croissant


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

