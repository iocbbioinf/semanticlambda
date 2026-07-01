"""Tests for lambda_parser.py.

Run from the project root:  .venv/bin/python tests/test_lambda_parser.py

Two layers:
  1. parse-equals   — parse(source) alpha-equals a hand-built LamTerm.
  2. parse-errors    — malformed source raises ParseError.
  3. parse-then-reduce — parse(source) fed through optimal_normal_form
                         matches the naive beta_reduce_sequence oracle.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimal_lambda import parse, ParseError
from optimal_lambda import beta_reduce_sequence
from optimal_lambda import optimal_normal_form
from test_sharing_graph import V, L, A, alpha_eq


PARSE_EQUALS = {
    "x": ("x", V("x")),
    "backslash-abs": (r"\x.x", L("x", V("x"))),
    "lambda-abs": ("λx.x", L("x", V("x"))),
    "app-juxtaposition": ("f a", A(V("f"), V("a"))),
    "app-left-assoc": ("f a b", A(A(V("f"), V("a")), V("b"))),
    "multi-arg-abs": (r"\f x.f x", L("f", L("x", A(V("f"), V("x"))))),
    "parens": (r"(\x.x) y", A(L("x", V("x")), V("y"))),
    "nested-abs-app": (
        r"\x.x x",
        L("x", A(V("x"), V("x"))),
    ),
    "free-and-bound": (
        r"\x.y (x z)",
        L("x", A(V("y"), A(V("x"), V("z")))),
    ),
    "whitespace-insensitive": (r"  \  x  .  x  x  ", L("x", A(V("x"), V("x")))),
}

PARSE_ERRORS = [
    "",
    "(x",
    "x)",
    "λ.x",
    "\\x.",
    "x 1a",
]

# parse(source) normalized via the GAL bus reducer vs. naive beta_reduce_sequence.
REDUCE_ORACLE = {
    "K-applied": (r"(\x y.x) a b", V("a")),
    "self-app-I": (r"(\x.x x) (\y.y)", L("y", V("y"))),
    "plus-2-3": (
        r"(\m n f x.m f (n f x)) (\f x.f (f x)) (\f x.f (f (f x)))",
        L("f", L("x", A(V("f"), A(V("f"), A(V("f"), A(V("f"), A(V("f"), V("x"))))))))),
}


def run() -> int:
    fails = 0

    print("=== parse-equals  parse(source) alpha== hand-built term ===")
    for name, (src, want) in PARSE_EQUALS.items():
        try:
            got = parse(src)
            ok = alpha_eq(got, want)
            print(f"  {name:24} {'OK' if ok else 'MISMATCH got ' + str(got)}")
            fails += not ok
        except Exception as ex:
            print(f"  {name:24} ERROR {type(ex).__name__}: {ex}")
            fails += 1

    print("=== parse-errors  malformed source raises ParseError ===")
    for src in PARSE_ERRORS:
        try:
            parse(src)
            print(f"  {src!r:24} FAIL: no error raised")
            fails += 1
        except ParseError:
            print(f"  {src!r:24} OK")
        except Exception as ex:
            print(f"  {src!r:24} FAIL: wrong exception {type(ex).__name__}: {ex}")
            fails += 1

    print("=== parse-then-reduce  optimal_normal_form(parse(source)) == oracle ===")
    for name, (src, want) in REDUCE_ORACLE.items():
        try:
            got = optimal_normal_form(parse(src))
            ok = alpha_eq(got, want)
            print(f"  {name:24} {'OK' if ok else 'MISMATCH got ' + str(got)}")
            fails += not ok
        except Exception as ex:
            print(f"  {name:24} ERROR {type(ex).__name__}: {ex}")
            fails += 1

    print(f"\n{'ALL PASS' if fails == 0 else str(fails) + ' FAILURE(S)'}")
    return fails


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
