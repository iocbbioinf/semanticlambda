---
name: optimal-lambda-reduction
description: Implementation reference for optimal lambda reduction via sharing graphs (interaction nets), based on Gonthier–Abadi–Lévy's "The Geometry of Optimal Lambda Reduction" (paper1), with efficiency caveats from Lawall–Mairson (paper2). Use when implementing, extending, or debugging an optimal/sharing-graph beta-reducer in this project.
---

# Optimal Lambda Reduction (Sharing Graphs / Interaction Nets)

This skill is a self-contained implementation guide distilled from two papers:

- **paper1** — Gonthier, Abadi, Lévy, *The Geometry of Optimal Lambda Reduction* (POPL 1992). The concrete algorithm. **Primary source.**
- **paper2** — Lawall, Mairson, *Optimality and inefficiency: what isn't a cost model of the lambda calculus?* (ICFP 1996). Read only for the efficiency reality-check that bounds expectations.

Sources on disk: `/home/marek/uochb/work/as/geometryOfOptimalLambdaReductionPDFA.pdf`, `/home/marek/uochb/work/as/shared lc - Optimality and inefficiency.pdf`.

**Sibling skills.** `understand-bus-optimal-reduction` is how the reducer *as
built* actually computes (bus form, R1–R6, BASE/OFFSET/COMMAND) — read it for the
code, and this skill for the conceptual background and the paper2 cost story.
Above the reducer sit two more layers with their own skills: **`reading`** (the
reading calculus — builds terms, **never reduces**) and **`ontology`** (models in
which a reading is valid; the reducer's client, though implemented on *terms*
rather than on bus graphs). If you are here to answer a question about readings,
pointers, contraction or ontologies, you are in the wrong skill.

## What "optimal" means and why bother

Naive beta reduction (what `optimal_lambda/term.py:lam_subst` does) **copies** the argument into every occurrence of the bound variable. When the argument is itself reducible and is duplicated *before* being reduced, the same work is redone in each copy — exponential blowup on terms like Church-numeral exponentiation.

Lévy's notion of optimality (1978): redexes that are "the same" (share a history / a label) should be reduced **once, together**, in a single parallel beta-step — never duplicated, never re-done. Lamping (1990) gave the first correct graph implementation; paper1 reconstructs and simplifies it through Girard's *geometry of interaction*.

**Key promise:** no redex family is ever reduced twice. **Key caveat (paper2):** this counts only *beta* work — the *bookkeeping* (index/bracket/croissant management) can itself be exponential. See "Reality check" below before promising speedups.

## The core idea: terms as interaction-net graphs

A lambda term is compiled to an **undirected graph of interaction-net nodes**. Reduction is purely **local graph rewriting**: two nodes whose *principal (interaction) ports* face each other on the same edge rewrite. No global traversal, no substitution. This is what makes sharing tractable.

Paper1 deliberately uses **fewer node kinds than Lamping**. There are NO separate abstraction (λ) or application (@) nodes — fan nodes do that job too. The node set is:

| Node | Arity | Role |
|------|-------|------|
| **fan** (fan-in `▽` / fan-out `△`) | 3 (ternary) | Sharing. A fan-in merges two wires into one shared subterm; a fan-out splits a shared value to two contexts. Both also stand in for λ and @. Symmetric: "fan-in" vs "fan-out" is just orientation. |
| **bracket** | 2 (unary in old form, ternary in bus form) | Bookkeeping: marks a region boundary; combines/splits two wires. Manipulates the deBruijn-like index. |
| **croissant** | 1 (unary) | Bookkeeping: creates a wire "ex nihilo"; marks a scope boundary that disappears under a β-step. |
| **root** | 1 | Terminates a dangling edge (free variables, the result edge). Carries variable names for read-back clarity only. |
| **void** (`⊗`) | 1 | A plug; terminates a dead wire (e.g. an unused argument branch). |

Every node carries a non-negative integer **index** (depth in the context). Indices are what let two facing fans decide whether they are "the same fan" (annihilate) or "different" (duplicate) — the central technical problem of optimal reduction. **All implementations (Lamping, GAL, Asperti) differ ONLY in how this index bookkeeping is maintained.**

### Two equivalent formalisms in paper1

Paper1 gives the graph in two interchangeable forms (Section 3):

1. **Indexed nodes** (Section 3.1–3.2, Figure 1): single-wire edges, each node tagged with an integer index. Reduction rules in Figure 1 assume `0 ≤ i < j`. **Recommended for a first implementation** — closest to a normal data structure.
2. **Bus form** (Section 3.3, Figure 2): each wire becomes a *bus* of `k` parallel wires; indices vanish because position-on-the-bus encodes them. More primitive, "commutation is free," but heavier to model. Use only if the indexed form's bookkeeping commutations become the bottleneck.

Use the **indexed form (Figure 1)** as the implementation target unless you have a specific reason not to.

## Reduction rules (the heart of it)

Reduction = local rewriting when two principal ports meet. From paper1 Figure 1 (indexed form), the families of rules are:

1. **Fan annihilation** — two fans with the **same index `i`** facing each other cancel: their wires fuse straight through (`)(` becomes `||`). This is the shared β-redex being consumed once. This is also how λ meets @ (the β-step itself).
2. **Fan duplication** — two fans with **different indices** facing each other **duplicate** each other (cross-wiring, the X-pattern). This propagates sharing.
3. **Bracket/croissant vs fan** — bookkeeping nodes pass through fans, **incrementing/decrementing indices** as they cross. A node's index increments when it passes to the left of a bracket and decrements when it passes a croissant.
4. **Bracket/croissant annihilation & commutation** — bracket meets bracket, croissant meets croissant, bracket meets croissant: adjust indices, sometimes annihilate, sometimes just commute past each other.

**Critical correctness detail (paper1 §3.1, also paper2 §3.2):** a croissant meeting a bracket does **NOT** simplify `+1 −1` to nothing in general — there is no rewrite rule collapsing the substring `+−` or `−+` to ε. The bookkeeping forms a redundant "base 1 without subtraction." This redundancy is *exactly* the bottleneck paper2 identifies; do not "optimize" it away naively or you will break correctness.

Because every rule is a local interaction-net rule (each node has one principal port), the system is **Church–Rosser / has no critical pairs** — the order of independent reductions doesn't affect the result, and parallel implementation is trivial.

**Deadlock:** some node pairs (e.g. bracket and fan with the same index) have no interaction rule. Meeting face-to-face = deadlock, which the context semantics shows corresponds to incompatible context constraints. A correct compiler should never produce a reachable deadlock for a well-typed λ-term.

## Translating a λ-term to a graph (paper1 §4.1)

Two stages:

1. **Incomplete graph, inductive on term structure.** Edges are directed (result edges drawn up, free-variable edges drawn down) and labelled with variable names.
   - **Variable** → a bus of width 3 (carries: base address / offset / command). In indexed form, a variable occurrence at index `k` bound by a λ at index `ℓ` is coded by a sequence of `k − ℓ` brackets; the sequence ends in a **croissant**.
   - **Abstraction `λx.M`** → graph of `M` with a fan handling the binding; brackets are added on all free-variable edges; a croissant opens the lexical scope, pushing the call offset onto the base address.
   - **Application `M N`** → graphs of `M` and `N` joined by a fan (the @ role); a top fan piles the "call" token. If `N`'s variable is shared with `M`, fan-ins combine the common references.
   - **Unused argument:** if `x` does not occur in `M` for `λx.M`, the argument branch of the fan is terminated by a **void** plug (the black branch is dead).
2. **Close the graph:** add root nodes for free variables and the result edge; strip variable names onto roots.

Note: this translation introduces fan-**in** nodes but sharing of fan-**out** appears only *during* reduction. There is no upfront common-subexpression detection — sharing emerges dynamically.

## Read-back (graph → λ-term) — paper1 §5.2, §6.1

You must recover a λ-term (or its Böhm tree) from a normal-form graph. This is the **read-back problem** and it is subtle (Lamping's proof is "laborious and somewhat ad hoc").

Practical algorithm from paper1 (Proposition 3, the "access-path shunting" / context-semantics route):
- Fans on the **rightmost wires** correspond to syntax-tree nodes (application / abstraction / variable).
- Read the Böhm tree by **following all downward paths consistent on all but the rightmost wire**, producing a syntax node for each rightmost fan encountered, oriented by edge direction.
- A variable is bound by the λ with the **same left context**.
- Edge orientation is recoverable purely from the **context semantics**, so it survives reduction.

The simple read-back `R` (Theorem 3): for any λ-term `M`, `R(G_M) = M`; reduction commutes with read-back; normal-form graph reads back to a normal form.

## Garbage collection — paper1 §6.2

- Local GC rules (à la Lamping) **destroy optimality** because they can't collect cyclic garbage without first unraveling it — so paper1 **omits local GC from the basic system**.
- Instead: by Proposition 3, reclaim all nodes **unreachable from the top root** in the *oriented* graph (Kathail-style). This standard collection is complete: in a graph with no accessible redexes, nodes are reachable from the top root iff there's an access path to them.
- Determining exact garbage (no accessible path) requires execution and is undecidable — so don't aim for perfect GC.

## Reality check before you promise performance (paper2)

Paper2 is the cold shower. Implement optimal reduction, but calibrate expectations:

- **Optimal ≠ efficient.** Optimal reduction guarantees no β-redex *family* is reduced twice, but the **bookkeeping** (brackets, croissants, index readjustment) can require **Ω(2ⁿ) interactions** to effect `n` parallel β-steps (paper2 Theorems 1, 3, 5, 6). Example: `Cₙ(λx.λy.xy)` normalizes in Θ(n) β-steps but GAL/Asperti/Lamping all take Ω(2ⁿ) bookkeeping steps.
- **The bottleneck is the irreducible `+−` bookkeeping** described above, not the β-work.
- **Local rules can't detect normal form** efficiently — there exist terms needing only Θ(n) parallel β-steps but Ω(2ⁿ) further fan interactions to certify normalization (paper2 Theorem 6 / §3).
- Optimal evaluators **fail the Frandsen–Sturtivant cost model** (paper2 §5.1): reducing to the Church numeral for 2^(2ⁿ) genuinely needs a Θ(2ⁿ)-node graph.
- **Implication for this project:** an optimal reducer is the right choice when sharing actually pays (duplicated reducible arguments — e.g. Church arithmetic, repeated function application). For terms with little sharing it can be *slower* than the naive `beta_step` in `optimal_lambda/term.py` due to bookkeeping overhead. Consider keeping both and choosing per-term, or benchmark before replacing the naive path.

## Status in THIS project: already implemented (bus form)

**This roadmap is done.** The reducer exists and passes its tests — but it was built in the **bus form (Figure 2)**, not the indexed form (Figure 1) that the rest of this skill describes. So treat the sections above as *conceptual background*; for the code as actually written, read the sibling skill **`understand-bus-optimal-reduction`**.

Code lives in the `optimal_lambda/` package (split out in commit `f501c1c`; there is no `sharing_graph.py` or top-level `lambda_term.py`):

| Roadmap step | Where it landed |
|---|---|
| 1. Keep the naive reducer as oracle | `optimal_lambda/term.py` — `LamVar`/`LamApp`/`LamAbs` (IRI-keyed), `lam_subst`, `beta_step`, `beta_reduce_sequence`. Unmodified; still ground truth. |
| 2. Node types & principal ports | `optimal_lambda/graph.py` — `NodeKind`, `SyntaxRole`, `Node`, `WireEnd`, `Graph.new_*`. Bus form, so nodes carry a **`slot`/`main` wire position instead of an `index: int`**. |
| 3. Compile `LamTerm → Graph` (§4.1) | `optimal_lambda/compile.py` — `compile_term`, `_Compiler`. |
| 4. Rules + driver | `optimal_lambda/rules.py` (`rule1…rule6`, `reduce_redex`), `optimal_lambda/redex.py` (`find_redexes`), `optimal_lambda/normalize.py` (`normalize`, `optimal_normal_form`). **Six Figure-2 schemas, not the indexed Figure-1 rules.** |
| 5. Read-back (§5.2/§6.1) | `optimal_lambda/readback.py` — `readback`, `_ReadBack`. |
| 6. Oracle test | `tests/test_sharing_graph.py` — 25 oracle + 8 round-trip; `KNOWN_FAILING` is empty. Plus `tests/test_lambda_parser.py` (19). |
| 7. Bookkeeping counters | `Graph.fan_interactions` (R1/R4, β-work) vs `Graph.book_interactions` (R2/R3/R5/R6). See paper2 blow-up on Church arithmetic. |

Extras beyond the original roadmap: `optimal_lambda/parser.py` (`parse` — source text → `LamTerm`), `optimal_lambda/context_semantics.py` (`validate_bus_rules` — symbolic §5.1 check on the rule wiring), `optimal_lambda/repl.py` + `__main__.py` (interactive REPL). Public API is re-exported from `optimal_lambda/__init__.py`.

Run the tests from the project root:

```
.venv/bin/python tests/test_sharing_graph.py
.venv/bin/python tests/test_lambda_parser.py
python -m optimal_lambda          # REPL
```

If you extend a rule or gadget, run **both** `validate_bus_rules` (rule wiring/semantics) and the oracle suite (compile/read-back), plus `Graph.check_integrity` / `Node.check_widths` — width arithmetic (R2/R5 +1, R3/R6 −1) is the usual source of silent breakage.

## Naming / vocabulary to keep consistent

fan (fan-in / fan-out), bracket, croissant, root, void, index, principal/interaction port, redex family, parallel β-step, access path, context semantics, read-back, Böhm tree, bus (only in bus-form). Use these exact terms in code and comments so the implementation maps cleanly back to paper1.
