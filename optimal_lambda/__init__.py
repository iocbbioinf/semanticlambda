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
  BRACKET    narrow (n) <-> wide (n+1); combines two adjacent wires at `slot`.
  CROISSANT  wide (n) <-> thin (n-1); creates/removes one wire at `slot`.

Reduction rules (paper Figure 2; numbering per project notes):
  R1 fan/fan        same wire   -> annihilate (grey-grey, black-black)
  R2 bracket/bracket same slot  -> annihilate on wide side  (width +1)
  R3 croissant/croissant same slot -> annihilate on thin side (width -1)
  R4 fan/fan        diff wires  -> 2x2 grid of four fans
  R5 fan/bracket    diff wires  -> commute; bracket duplicates; width +1
  R6 fan/croissant  diff wires  -> commute; croissant duplicates; width -1

Module layout:
  term               the LamTerm AST + naive beta_reduce_sequence oracle
  parser             lambda-calculus source text -> LamTerm
  graph              bus geometry: Node, WireEnd, Graph, slot arithmetic
  redex              active-pair detection (find_redexes)
  rules              the six Figure-2 rewrites + reduce_redex
  context_semantics  symbolic §5.1 validation of the rules
  compile            LamTerm -> bus graph
  readback           bus graph -> LamTerm
  normalize          reduction driver + optimal_normal_form

Typical use:
    from optimal_lambda import parse, optimal_normal_form
    print(optimal_normal_form(parse(r"(\\x.x x) (\\y.y)")))
"""

from __future__ import annotations

from .term import (
    LamTerm, LamVar, LamApp, LamAbs,
    lam_to_dict, lam_from_dict, lam_subst,
    beta_step, beta_reduce_sequence,
)
from .parser import parse, ParseError
from .graph import Graph, Node, WireEnd, NodeKind, SyntaxRole
from .redex import Redex, find_redexes
from .rules import reduce_redex
from .context_semantics import Ctx, validate_bus_rules
from .compile import compile_term
from .readback import readback
from .normalize import normalize, optimal_normal_form

__all__ = [
    "LamTerm", "LamVar", "LamApp", "LamAbs",
    "lam_to_dict", "lam_from_dict", "lam_subst",
    "beta_step", "beta_reduce_sequence",
    "parse", "ParseError",
    "Graph", "Node", "WireEnd", "NodeKind", "SyntaxRole",
    "Redex", "find_redexes", "reduce_redex",
    "Ctx", "validate_bus_rules",
    "compile_term", "readback",
    "normalize", "optimal_normal_form",
]
