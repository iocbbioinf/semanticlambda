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

def church(n: int):
    body = V("x")
    for _ in range(n):
        body = A(V("f"), body)
    return L("f", L("x", body))


two = church(2)
three = church(3)
succ = L("n", L("f", L("x", A(V("f"), A(A(V("n"), V("f")), V("x"))))))
plus = L("m", L("n", L("f", L("x",
        A(A(V("m"), V("f")), A(A(V("n"), V("f")), V("x")))))))
mult = L("m", L("n", L("f", A(V("m"), A(V("n"), V("f"))))))

ORACLE = {
    "I": I,
    "Iy": A(I, V("y")),
    "II": A(I, L("z", V("z"))),
    "Ky": A(K, V("y")),
    "K": K,
    "Kab": A(A(K, V("a")), V("b")),
    "K2ab": A(A(L("x", L("y", V("y"))), V("a")), V("b")),
    "KIy": A(A(K, I), V("y")),
    # sharing / duplication
    "dupI": A(L("x", A(V("x"), V("x"))), I),
    "dup2": A(L("x", A(V("x"), V("x"))), A(I, I)),
    "dupK": A(L("x", A(V("x"), V("x"))), K),
    "dup_dup": A(L("x", A(V("x"), V("x"))),
                 A(L("y", A(V("y"), V("y"))), I)),
    # Church numerals / arithmetic
    "two_I_z": A(A(two, I), V("z")),
    "three_I_z": A(A(three, I), V("z")),
    "two_f_x": A(A(two, V("f")), V("x")),
    "three_K_a": A(A(three, K), V("a")),
    "succ_two": A(A(A(succ, two), V("f")), V("x")),
    "plus_2_3": A(A(A(A(plus, two), three), V("f")), V("x")),
    # deep sharing: Church multiplication — these formerly REDUCED fine but
    # the structural read-back cycled (depth guard) because it could not
    # unfold a body shared by several occurrences. Now read back by the
    # context-stack discipline in _ReadBack.
    "mult_2_3": A(A(mult, two), three),
    "mult_3_3": A(A(mult, three), three),
    "mult_2_2_2": A(A(mult, A(A(mult, two), two)), two),
    # combinators
    "SKKz": A(A(A(S, K), K), V("z")),
    "SIIw": A(A(A(S, I), I), V("w")),
}

KNOWN_FAILING: dict = {}


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
