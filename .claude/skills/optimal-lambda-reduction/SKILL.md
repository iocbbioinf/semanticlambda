---
name: optimal-lambda-reduction
description: Implementation reference for optimal lambda reduction via sharing graphs (interaction nets), based on Gonthier–Abadi–Lévy's "The Geometry of Optimal Lambda Reduction" (paper1), with efficiency caveats from Lawall–Mairson (paper2). Use when implementing, extending, or debugging an optimal/sharing-graph beta-reducer in this project.
---

# Optimal Lambda Reduction (Sharing Graphs / Interaction Nets)

This skill is a self-contained implementation guide distilled from two papers:

- **paper1** — Gonthier, Abadi, Lévy, *The Geometry of Optimal Lambda Reduction* (POPL 1992). The concrete algorithm. **Primary source.**
- **paper2** — Lawall, Mairson, *Optimality and inefficiency: what isn't a cost model of the lambda calculus?* (ICFP 1996). Read only for the efficiency reality-check that bounds expectations.

Sources on disk: `/home/marek/uochb/work/as/geometryOfOptimalLambdaReductionPDFA.pdf`, `/home/marek/uochb/work/as/shared lc - Optimality and inefficiency.pdf`.

## What "optimal" means and why bother

Naive beta reduction (what `lambda_term.py:lam_subst` does today) **copies** the argument into every occurrence of the bound variable. When the argument is itself reducible and is duplicated *before* being reduced, the same work is redone in each copy — exponential blowup on terms like Church-numeral exponentiation.

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
- **Implication for this project:** an optimal reducer is the right choice when sharing actually pays (duplicated reducible arguments — e.g. Church arithmetic, repeated function application). For terms with little sharing it can be *slower* than the naive `beta_step` already in `lambda_term.py` due to bookkeeping overhead. Consider keeping both and choosing per-term, or benchmark before replacing the naive path.

## Implementation roadmap for THIS project

The existing `lambda_term.py` has `LamVar`/`LamApp`/`LamAbs` (IRI-keyed) and a naive `lam_subst`/`beta_step`. To add optimal reduction:

1. **New module** (e.g. `sharing_graph.py`) — do **not** modify the naive reducer; keep it as the reference oracle for testing.
2. Define interaction-net node types: `Fan`, `Bracket`, `Croissant`, `Root`, `Void`, each with `index: int` and a small fixed set of ports (track which port is the principal/interaction port).
3. **Compile** `LamTerm → Graph` per §4.1 above. Use the existing IRIs as stable identifiers for variables and λ-binders so the graph links back to the RDF/KG layer.
4. Implement the **indexed-form rules** (Figure 1): a worklist of edges whose two endpoints have facing principal ports; pop, apply the matching rule, push newly-adjacent pairs. Loop until no active pair.
5. Implement **read-back** (§5.2/§6.1) `Graph → LamTerm`.
6. **Test by oracle:** for many random closed terms, assert `readback(normalize(compile(t))) == beta_reduce_sequence(t)[-1]` (up to α-equivalence / IRI renaming). The naive reducer in `lambda_term.py` is ground truth on terms small enough for it to terminate.
7. Add a **bookkeeping-step counter** so you can empirically reproduce paper2's blowup on `Cₙ(λx.λy.xy)` and document where the implementation sits.

## Naming / vocabulary to keep consistent

fan (fan-in / fan-out), bracket, croissant, root, void, index, principal/interaction port, redex family, parallel β-step, access path, context semantics, read-back, Böhm tree, bus (only in bus-form). Use these exact terms in code and comments so the implementation maps cleanly back to paper1.
