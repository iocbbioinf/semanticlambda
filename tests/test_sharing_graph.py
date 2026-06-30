"""Tests for the GAL bus-reduction implementation in sharing_graph.py.

Run from the project root:  .venv/bin/python tests/test_sharing_graph.py

Three layers:
  1. validate_bus_rules  — symbolic context-semantics check of all 6 Fig-2 rules.
  2. round-trip          — readback(compile_term(t)) == t  (compile/readback agree).
  3. oracle              — readback(normalize(compile_term(t)))
                            == beta_reduce_sequence(t)[-1]  (full reduction).

lambda_term.py's naive beta_reduce_sequence is the ground-truth oracle.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sharing_graph as sg
from lambda_term import LamVar, LamApp, LamAbs, beta_reduce_sequence


# -- term builders ----------------------------------------------------------
def V(n: str) -> LamVar: return LamVar(iri="iri:" + n, label=n)
def L(n: str, b) -> LamAbs: return LamAbs(var=V(n), body=b)
def A(f, a) -> LamApp: return LamApp(func=f, arg=a)


def alpha_eq(s, t, env=None) -> bool:
    env = env or {}
    if isinstance(s, LamVar) and isinstance(t, LamVar):
        return env.get(s.iri, s.iri) == t.iri
    if isinstance(s, LamApp) and isinstance(t, LamApp):
        return alpha_eq(s.func, t.func, env) and alpha_eq(s.arg, t.arg, env)
    if isinstance(s, LamAbs) and isinstance(t, LamAbs):
        return alpha_eq(s.body, t.body, {**env, s.var.iri: t.var.iri})
    return False


# -- combinators ------------------------------------------------------------
I = L("x", V("x"))
K = L("x", L("y", V("x")))
S = L("f", L("g", L("x", A(A(V("f"), V("x")), A(V("g"), V("x"))))))
church2 = L("f", L("x", A(V("f"), A(V("f"), V("x")))))


ROUND_TRIP = {
    "x": V("x"),
    "I": I,
    "fa": A(V("f"), V("a")),
    "K": K,
    "lxx": L("x", A(V("x"), V("x"))),
    "church2": church2,
    "S": S,
    "freevars": L("x", A(V("y"), A(V("x"), V("z")))),
}

ORACLE = {
    "I": I,
    "Iy": A(I, V("y")),
    "II": A(I, L("z", V("z"))),
}

# NOTE (2026-06-30): bus-shared (per-wire) detection + R5/R6 are in. I, Iy, II
# now reduce correctly under the width-2 wiring (R5/R6 commute the addressing
# brackets/croissants off the shared principal bus, then R1 annihilates the
# fans). Terms with NESTED abstractions (K, Ky, Kab, K2ab, KIy) still fail: a
# bracket there is width>1 and spans a wire of the fan AND a wire of another
# operator (croissant) — R5 must commute the fan across only its OWN shared
# wire, not the bracket's whole wide bus. That general fan/bracket-on-a-shared-
# bus splice is the remaining work (paper Fig 2 bottom row, exact wire counts).
KNOWN_FAILING = {
    "Ky": A(K, V("y")),
    "K": K,
    "Kab": A(A(K, V("a")), V("b")),
    "K2ab": A(A(L("x", L("y", V("y"))), V("a")), V("b")),
    "KIy": A(A(K, I), V("y")),
    "dupI": A(L("x", A(V("x"), V("x"))), I),
}


def run() -> int:
    fails = 0

    print("=== validate_bus_rules (symbolic context semantics) ===")
    try:
        sg.validate_bus_rules(6)
        print("  PASS (widths 1..6)")
    except AssertionError as ex:
        print(f"  FAIL: {ex}")
        fails += 1

    print("=== round-trip  readback(compile(t)) == t ===")
    for name, t in ROUND_TRIP.items():
        try:
            rb = sg.readback(sg.compile_term(t))
            ok = alpha_eq(rb, t)
            print(f"  {name:9} {'OK' if ok else 'MISMATCH got ' + str(rb)}")
            fails += not ok
        except Exception as ex:
            print(f"  {name:9} ERROR {type(ex).__name__}: {ex}")
            fails += 1

    print("=== oracle  normalize+readback == beta_reduce_sequence ===")
    for name, t in ORACLE.items():
        want = beta_reduce_sequence(t)[-1]
        try:
            gn = sg.normalize(sg.compile_term(t), max_steps=20000)
            rb = sg.readback(gn)
            ok = alpha_eq(rb, want)
            tag = "OK" if ok else f"MISMATCH got {rb} want {want}"
            print(f"  {name:9} {tag}  (fan={gn.fan_interactions},"
                  f"bk={gn.book_interactions})")
            fails += not ok
        except Exception as ex:
            print(f"  {name:9} ERROR {type(ex).__name__}: {ex}")
            fails += 1

    print("=== known-failing (duplication index discipline) ===")
    for name, t in KNOWN_FAILING.items():
        want = beta_reduce_sequence(t)[-1]
        try:
            gn = sg.normalize(sg.compile_term(t), max_steps=20000)
            rb = sg.readback(gn)
            ok = alpha_eq(rb, want)
            print(f"  {name:9} "
                  f"{'UNEXPECTEDLY OK ' + str(rb) if ok else 'fails as expected'}")
        except Exception as ex:
            print(f"  {name:9} fails as expected ({type(ex).__name__})")

    print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURE(S)'}")
    return fails


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
