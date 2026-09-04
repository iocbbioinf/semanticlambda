---
name: understand-bus-optimal-reduction
description: Comprehension guide to paper1's OPTIMAL LAMBDA REDUCTION algorithm in its BUS formulation (Gonthier–Abadi–Lévy §3.3–3.4, Figure 2), and how it maps to the working reducer in the optimal_lambda/ package. Use when reading, explaining, reasoning about, extending, or debugging the bus-of-wires algorithm in this project — i.e. any question of the form "how/why does the bus reduction work", "what does this fan/bracket/croissant do", "why does term X reduce/read-back the way it does". For the from-scratch implementation roadmap and paper2 efficiency caveats, see the sibling skill `optimal-lambda-reduction`.
---

# Understanding the GAL Bus Algorithm for Optimal Lambda Reduction

This skill explains **how paper1's optimal reducer works in its bus form**, and ties every concept to the **working** implementation in the `optimal_lambda/` package. It is a *reading/reasoning* companion, not an implementation checklist — the algorithm is already built and passes 25 oracle + 8 round-trip tests. Use it to answer "why does this work" and "where in the code is this."

**Who consumes this reducer.** Two layers sit on top, and they use it very differently. The **reading** calculus (skill `reading`) never reduces at all — it only builds terms. The **ontology** layer (skill `ontology`) is the reducer's real client, but it is implemented **on terms, not on this bus graph**: `optimal_lambda.normalize` is never called from it, because every operation §8 needs ("is rule 1 applicable at `p`", "fire it, then all silent reductions") is at that scale a statement about the term. That seam is documented at the top of `ontology_state.py`. So the rule *numbering* R1–R6 below is shared vocabulary across all three skills, while the bus machinery itself currently has no runtime caller besides the tests and the REPL. Keep that in mind before "fixing" an apparent inconsistency between this skill and `ontology`.

- **paper1** — Gonthier, Abadi, Lévy, *The Geometry of Optimal Lambda Reduction* (POPL 1992). Sources on disk: `/home/marek/uochb/work/as/geometryOfOptimalLambdaReduction.pdf` (and `…PDFA.pdf`). The bus material is **§3.3–3.4 + Figure 2 (p.6)** and the λ-translation is **§4.1 (p.6–7)**; context semantics is **§5.1 (p.8)**.
- **Implementation** — the **`optimal_lambda/` package** (split out of the old single `sharing_graph.py` in commit `f501c1c`; neither `sharing_graph.py` nor a top-level `lambda_term.py` exists any more). Public API is re-exported from `optimal_lambda/__init__.py`, whose docstring carries its own module map.

  | Module | Contents |
  |---|---|
  | `term.py` | the `LamTerm` AST + **naive oracle** (`lam_subst`, `beta_step`, `beta_reduce_sequence`) |
  | `parser.py` | `parse` — source text → `LamTerm`; `ParseError` |
  | `graph.py` | bus geometry: `Graph`, `Node`, `WireEnd`, `NodeKind`, `SyntaxRole`, `BASE`/`OFFSET`/`COMMAND`, `INITIAL_ROOT_WIDTH`, slot arithmetic (`expand_slot_across_bracket`, `compress_slot_across_croissant`) |
  | `redex.py` | active-pair detection: `find_redexes`, `Redex`, `_classify_at`, `_principal_bus_clean`, `_principal_wire`, `_locate` |
  | `rules.py` | the six Figure-2 rewrites `rule1…rule6`, `reduce_redex`, `_annihilate`, `_capture_ports`, `_finalize` |
  | `context_semantics.py` | `Ctx`, `validate_bus_rules` — symbolic §5.1 validation |
  | `compile.py` | `compile_term`, `_Compiler`, `Fragment` |
  | `readback.py` | `readback`, `_ReadBack`, `read_branch`, `_read_fanin` |
  | `normalize.py` | `normalize`, `optimal_normal_form`, `_splice_joints` |
  | `repl.py`, `__main__.py` | interactive REPL (`python -m optimal_lambda`) |

  Tests, from project root: `.venv/bin/python tests/test_sharing_graph.py` and `.venv/bin/python tests/test_lambda_parser.py`.
- **Sibling skill** — `optimal-lambda-reduction` covers the *indexed* form (Figure 1), the implementation roadmap, and paper2's efficiency reality-check. Read that for "should I build this / what will it cost"; read *this* for "how does the bus version actually compute."

## 1. Why buses at all (§3.3)

In the indexed form (Figure 1) every wire is a single edge and every node carries an integer **index** = its depth in the context. The central annoyance: a row of brackets and croissants only reaches normal form after many *bureaucratic* commutations, and indexed rules like "bracket meets croissant with different indices" must be spelled out case by case. Bringing such a row to normal form can take quadratic time in uninteresting reshuffling (paper, top of p.5).

The **bus** idea (§3.3, "Decomposing the operators"): replace a single wire by a **bus** = a bundle of `width` parallel wires, and let each operator act on **one physical wire (a slot) of the bus**. The index disappears because *position on the bus encodes it*. Two bookkeeping operators sitting on **different wires of the same bus** simply cross without interacting — "the commutation is free" (paper p.6). That is the whole payoff: the quadratic bureaucratic reshuffle of the indexed form becomes free parallel wire-crossing.

Mental model to keep: **a bus is a maximal bundle of parallel wires running between operator boundaries; an operator is a little gadget clamped onto one (fan, croissant) or two adjacent (bracket) wires of that bundle; the other wires flow past untouched.**

## 2. The five node kinds, in bus form (§3.1 + §3.3)

Cross-reference: `NodeKind` and the `new_*` constructors in `optimal_lambda/graph.py` (`Graph.new_root/new_void/new_fan/new_bracket/new_croissant`, plus `new_joint`/`new_open_edge`).

| Node | Bus shape (ports & widths) | Role | Paper | Code |
|------|---------------------------|------|-------|------|
| **root** `⊤` | width-3 interface bus `[BASE, OFFSET, COMMAND]` | Terminates the result edge and each free-variable edge. Carries a variable name for read-back only. | §3.1, p.6 | `new_root`, `INITIAL_ROOT_WIDTH=3` |
| **void** `⊗` | a plug of any width | Terminates a dead wire (unused-argument branch). | §3.1 | `new_void` |
| **fan** | 3 equal-width ports `principal / grey / black`, each width `n`; one distinguished slot `main` where the mark sits | Sharing **and** λ/@ (no separate abstraction/application nodes). "New fan of arity 3n" (p.6). The **main level** carries the mark (grey `∘` on one branch, black `⋆` on the other); other levels don't touch contexts. | §3.3, Fig 2, p.6 | `new_fan(width, main, …)`, `SyntaxRole` tag APP/LAM/VAR/INTERNAL |
| **bracket** | ternary: `narrow` width `n` ↔ `wide` width `n+1`, at `slot` | **Combines two adjacent wires into one** (`wide[slot], wide[slot+1] ≙ narrow[slot]`). Manipulates address structure. | §3.3, p.5 | `new_bracket(narrow_width, slot)` |
| **croissant** | unary-ish: `wide` width `n` ↔ `thin` width `n-1`, at `slot` | **Creates/removes one wire ex nihilo** (a context box; the empty context `□`). | §3.3, p.5 | `new_croissant(wide_width, slot)` |

Plus an implementation-only helper: **joint** (`NodeKind.JOINT`, `new_joint`) — a degree-2 transparent pass-through used *at compile time* to give a variable-occurrence edge an identity; **`_splice_joints` removes them all before reduction** so they never block detection. Joints are not in the paper.

**Interaction (principal) ports** — which wire a node offers for a reaction:
- FAN → `principal[main]` (`_principal_wire`)
- BRACKET → `narrow[slot]`
- CROISSANT → `wide[slot]`

## 3. The three wire roles: BASE / OFFSET / COMMAND

This is the single most load-bearing design decision in the implementation and it is **not spelled out in the paper** — it was reconstructed and verified by round-trip + oracle. `optimal_lambda/graph.py` constants:

```
BASE    = 0   # base address, shared by all commands of one lexical-scope call
OFFSET  = 1   # call offset  (and: the wire the sharing fan-in acts on)
COMMAND = 2   # the actual command, piled on its subcommands
```

Paper §4.1 (p.7): a variable is a **bus of width 3** carrying *commands* between an occurrence and the value — "call a function", "return", "access an argument", "report a value". A command has three parts: **base address** (shared across a lexical scope), **offset** (within the scope), and the **command** itself piled on its subcommands. Those are the three wires.

The project then assigns each wire a **job**, so that operators meeting on different jobs *commute* (R5/R6) instead of *deadlocking*:

- **BASE (slot 0)** — call/scope **addressing** brackets & croissants live here, built inline in `_Compiler.app` / `_Compiler.abs` from the `_fold` / `_unfold` / `_croissant_new` helpers.
- **OFFSET (slot 1)** — the **sharing fan-in** for a shared variable acts here (`_fan_in`, an arity-3 INTERNAL fan marked on the middle wire).
- **COMMAND (slot 2)** — the **syntactic** λ/@ fans (the APP and LAM gadgets) act here.

Why it matters (the debugging lesson): put the sharing fan-in and an abstraction fan on the *same* wire and they meet with the same `main` → **R1 annihilate** (consume the shared value once, wrong). Put them on *different* wires and they meet with different `main` → **R4 duplicate** (copy the shared value into each context, right). Getting `(λx.xx)I` to duplicate `I` into both occurrences instead of destroying it was exactly the fix of moving the sharing fan-in to OFFSET while λ/@ fans stay on COMMAND. If you are debugging a term that *annihilates when it should duplicate* (or vice-versa), suspect wire-role placement first.

## 4. The six reduction rules (Figure 2, p.6)

Reduction = local rewriting when two operators meet **principal-wire to principal-wire on a shared bus**. There are exactly six schemas. Numbering follows the project notes and is confirmed against the Figure-2 image; the general case of each is obtained by varying bus width (paper: "the matched region may be embedded in a larger bus").

| # | Meeting | Effect | Width | Code |
|---|---------|--------|-------|------|
| **R1** | fan / fan, **same** wire (`main_a == main_b`) | **Annihilate.** Wires fuse straight through; grey↔grey, black↔black. This is the shared β-redex consumed once, *and* the β-step when APP fan meets LAM fan. | unchanged | `rule1_fan_fan_same` → `_annihilate` |
| **R2** | bracket / bracket, **same** slot | Annihilate on the wide side. | +1 between them | `rule2_…` → `_annihilate` |
| **R3** | croissant / croissant, **same** slot | Annihilate on the thin side. | −1 | `rule3_…` → `_annihilate` |
| **R4** | fan / fan, **different** wires (`i=bottom.main`, `j=top.main`, canonicalized `i<j`) | **Duplicate.** Replace by a **2×2 grid of four fans**, cross-wired `bottomOnTop[p].q ↔ topOnBottom[q].p`. This propagates sharing. | unchanged | `rule4_fan_fan_diff` |
| **R5** | fan / bracket, **different** wires (`fan.main ≠ bracket.slot`) | **Commute:** fan crosses the bracket; bracket **duplicates** onto the fan's grey & black branches; fan `main` shifts by `expand_slot_across_bracket`. | +1 | `rule5_fan_bracket_diff` |
| **R6** | fan / croissant, **different** wires (`fan.main ≠ croissant.slot`) | **Commute:** dual of R5; croissant duplicates onto both branches; `main` shifts by `compress_slot_across_croissant`. | −1 | `rule6_fan_croissant_diff` |

**Deadlock** (paper p.4): a fan meeting a bracket/croissant with `fan.main == slot` has no rule. The context semantics shows this is exactly an **incompatible context constraint**; a correct compiler never produces a reachable deadlock for a well-typed term. In code, `_classify_at` returns deadlock in that case.

**Church–Rosser / no critical pairs:** because each node has one principal wire, the order of independent reductions doesn't change the result. Optimality needs a *strategy* only to avoid wasted work, not for correctness — leftmost-outermost / normal order suffices (paper p.5, "simple strategies will do"). `find_redexes` returns leftmost-first; `normalize` loops.

Counters mirror the paper2 story: `g.fan_interactions` (R1, R4 — the β-ish work) vs `g.book_interactions` (R2, R3, R5, R6 — the bookkeeping). Watch these grow super-linearly on Church arithmetic (e.g. `plus_2_3` → fan=8, book=209): that is paper2's bookkeeping blow-up, not a bug.

### Two subtleties the implementation had to solve (not obvious from the paper)

1. **Bus-shared / per-wire detection.** An operator sits on **one wire** of a possibly-wide shared bus; the bus's *other* wires fan out to *different* nodes. So an "active pair" is **not** two whole ports wired together (interaction-net style) — it is two operators whose *principal wires* lie on the same bus. `_classify_at` follows a node's principal wire to its peer and classifies by what it finds; `_principal_bus_clean` gates fan/fan R1/R4 so that any bookkeeping spliced onto the shared principal bus **commutes away first** (via R5/R6 detected from the bracket/croissant side) before the two fans fuse. If you see reduction "fire 0 rules" on a compiled term, this per-wire vs whole-port distinction is the classic cause.

2. **Loop-aware rules.** After duplication, a body like `λx.x` becomes a fan whose grey and black branches loop **back to each other** (a self-loop). The rewrite rules must re-tie such loops rather than double-wiring them. R1/R2/R3 use a **union-find over saved peers** (`_annihilate`) that also unions each operator's internal grey↔black self-loops; R4/R5/R6 use `_capture_ports` (returns per-wire `('ext', peer)` or `('loop', port, slot)`) + `_finalize` to reconnect. This is what makes `SII`, nested duplication, and Church numerals work without `KeyError` crashes.

## 5. Compiling a λ-term to a bus graph (§4.1, p.6–7)

`compile_term` / class `_Compiler`. Two stages: build an incomplete directed graph inductively, then close it with roots (result edge up, free-variable edges down; variable names moved onto roots).

The exact BASE/OFFSET/COMMAND routing is **informal in the paper** ("the translation is presented only informally") and was reconstructed to satisfy round-trip `readback(compile(t)) == t`. Authoritative wiring as built:

- **Variable** → an open bus of width 3 `[BASE, OFFSET, COMMAND]` (`var`, via `new_open_edge`). Paper: "a variable is represented with a bus of width 3."
- **Application `M N`** (`app`) — a **fan-in**, width 2 (the 3 root wires fold to 2), `main = 1`. Principal points **down** to the function `G`: `principal[0] → G.BASE`, `principal[1] → G.COMMAND`, and `G.OFFSET` is created fresh by `_croissant_new()` (a null offset). **grey** = the result edge (up), width 3 via `_unfold(grey[1])` → OFFSET+COMMAND; **black** = the argument `H` (up), with `_fold(H.OFFSET, H.COMMAND)` onto `black[1]`. Marked on COMMAND. Shared free variables of `M` and `N` are merged by `_fan_in` (via `_merge_free`).
- **Abstraction `λx.M`, `x ∈ FV(M)`** (`abs`) — a **fan-out**, width 2, `main = 1`, dual to the application fan, marked on COMMAND. **grey** goes to the body `G` (`grey[0] → G.BASE`, `_fold(G.OFFSET, G.COMMAND) → grey[1]`); **black** is the bound variable `x` and **loops around** to `x`'s occurrence inside `G` (same fold). Principal is the result edge, width 3 via `_unfold(principal[0])` → BASE+OFFSET. Every free variable `y` of `G` crossing the λ gets a **scope boundary on its BASE wire** — built inline as `ll, lr = _unfold(y.BASE)`, then `y.OFFSET = _fold(lr, y.OFFSET)` ("brackets added to all free-variable edges", p.7).
- **Abstraction `λx.M`, `x ∉ FV(M)`** — same, but the bound-variable (black) branch is terminated by a **void** plug (p.7: "the black branch is effectively dead, terminate it by a plug").
- **Shared free variable** → `_fan_in`: an **arity-3 INTERNAL fan** (not width-2), all three wires straight through on grey (use in `M`) / black (use in `N`) / principal (merged), **marked on OFFSET (slot 1)**. This is the sharing combinator; keeping it full-width-3 on OFFSET (rather than folding it like the syntactic fans) is a deliberate, tested choice.

Helpers (all `_Compiler` methods in `optimal_lambda/compile.py`): `_fold(a,b)` = a 1-narrow/2-wide bracket combining two wires into one; `_unfold(w)` = its inverse orientation; `_croissant_new()` = a wide-1/thin-0 croissant creating a fresh wire ex nihilo. `Fragment` carries `result` / `free` / `labels` up the recursion. There is **no** up-front common-subexpression detection — sharing (fan-out) emerges only *during* reduction.

## 6. Reading a graph back to a λ-term (§5.2, §6.1)

`readback` / class `_ReadBack`. Recover the term (Böhm tree) from a normal-form graph by walking down from the top root.

- **Transport** (`_walk`): follow the COMMAND / rightmost wire; **JOINT, BRACKET, CROISSANT are transparent** pass-throughs (slot clamped to the command wire); branch only at **syntactic λ/@ fans**. This permissive structural walk is load-bearing — it deliberately ignores the full PAIR/BOX bus-context structure, which over-constrains transport (an attempt to carry full contexts broke 21 cases and was reverted).
- **Syntax fans** produce nodes: an **APP** fan reached via its principal → `App(grey-branch, black-branch)`; a **LAM** fan reached via its principal → `Abs(var, grey-branch)`. Reaching a LAM fan via its **black** branch = an **occurrence** of that binder's variable (tracked in a `binder_var` map). A variable is bound by the λ with the **same left context**.
- **Sharing fan-ins are the one place context matters** (`read_branch` threads `stacks: dict[level → list[GREY/BLACK]]`, keyed by the fan-in's `main` level). `_read_fanin`: entered via grey/black from an occurrence **pushes** that mark for the level and descends out the principal into the shared body; entered via principal **pops** the level's top mark to choose which occurrence's branch to resume. LIFO per level → independent sharings don't interfere and nested same-level fan-ins unfold correctly. **This per-level context stack is the fix that made deep Church `mult` read back correctly** (commit `8f00d1b`); it is exactly the GAL context discipline of §5.1 restricted to just the grey/black marks read-back needs.

Read-back is iterative over sharing, so Python recursion depth grows only with term *depth*, not sharing (`_max_visits` guard = 5,000,000).

## 7. Correctness ground truth: context semantics (§5.1) + the oracle

Two independent checks, both in the repo:

1. **Symbolic rule validation** — `validate_bus_rules(max_width)` builds context trees (`Ctx`: `BOX □`, `GREY ∘.a`, `BLACK ⋆.a`, `PAIR ⟨a,b⟩`) and checks that **all six Figure-2 rules preserve the §5.1 context semantics** for buses up to `max_width`. A fan maps a wire-context `b → ∘.b` (grey) / `⋆.b` (black) at its main slot; a bracket combines adjacent `a,b → ⟨a,b⟩`; a croissant inserts `□`. **This is the authority for whether the rule wiring is right.**
2. **Oracle round-trip** — for each term, `readback(normalize(compile_term(t)))` must equal `term.beta_reduce_sequence(t)[-1]` up to α-equivalence / IRI renaming. The naive reducer in `optimal_lambda/term.py` is ground truth. 25 oracle terms pass (I, K, S combinators; `dupI`; Church numerals; `succ`, `plus`, and deep `mult 2 3 / 3 3 / 2 2 2`). `KNOWN_FAILING` is empty.

If you change a rule or a gadget, **run both**: `validate_bus_rules` catches wiring/semantics regressions; the oracle catches compile/readback regressions. Also run `Graph.check_integrity` / `Node.check_widths` after edits — width arithmetic (R2/R5 +1, R3/R6 −1) is the usual source of silent breakage.

## 8. Vocabulary — keep it consistent with paper and code

bus (bundle of wires) · width · slot · wire · main (main level / marked slot) · principal wire · fan (fan-in / fan-out; grey `∘` / black `⋆`) · bracket (narrow ↔ wide, combines two adjacent wires) · croissant (wide ↔ thin, wire ex nihilo, `□`) · root · void (plug) · joint (compile-time pass-through, spliced away) · BASE / OFFSET / COMMAND · active pair / redex · R1…R6 · annihilate / duplicate / commute · context semantics (`□`, `∘.a`, `⋆.a`, `⟨a,b⟩`) · access path · read-back · Böhm tree · fan_interactions vs book_interactions.

## 9. Fast pointers for a returning session

- Rule wiring wrong → `context_semantics.py:validate_bus_rules` + `rules.py` (`_annihilate` / `rule4…` / `rule5…` / `rule6…`).
- "Fires 0 rules" → per-wire detection in `redex.py`: `_classify_at`, `_principal_bus_clean`, `_locate`, `_principal_wire`.
- Annihilates when it should duplicate → wire-role placement (BASE/OFFSET/COMMAND) in `compile.py:_Compiler`; sharing fan-in must be on OFFSET, λ/@ on COMMAND.
- Read-back cycles / wrong on shared terms → the per-level stack in `readback.py`: `read_branch` / `_read_fanin`.
- Crash after duplication (`KeyError`, double-wiring) → loop handling in `rules.py`: `_capture_ports` / `_finalize` / union-find in `_annihilate`.
- Redexes detected but joints in the way → `normalize.py:_splice_joints` (runs once up front, inside `normalize`).
- Full narrative of every fix, in order, is in memory `optimal-lambda-reduction-impl` (progress notes #1–#11).
