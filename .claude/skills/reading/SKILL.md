---
name: reading
description: The Reading calculus and its Ontology layer — this project's model of a user reading a knowledge graph as a typed applicative lambda term, with ontologies as models in which the reading is valid. Covers the operations (contraction cases 1/2/4, reflection), the typing-as-location rule, the epistemic design principle, verified expressiveness results, and how it all maps onto the bus reducer in optimal_lambda/. Use when reading, explaining, extending, implementing or debugging anything about readings, pointers, contraction, reflection, questions, or ontologies — including "why is there no case 3/5", "why does case 2 adopt Pr", "what can a reading build", "how do ontologies get filtered".
---

# The Reading Calculus and its Ontology Layer

This project models **a user reading a knowledge graph** as the incremental construction of a typed applicative lambda term, together with a set of **ontologies** — models in which that reading is valid, obtained by reduction in the GAL bus reducer.

**Authoritative specification, do not duplicate — read these files:**

- `notes/reading_desc` — the consolidated prose spec (§1–§10): what a reading is, typing, the design principle, every operation, invariants, the ontology concept, worked example, open points, expressiveness results.
- `notes/reading_alg` — language-agnostic pseudocode: data structures, typing, legality, each operation, ontology procedures, driver, invariant check, implementer notes.

Sibling skills: `understand-bus-optimal-reduction` (how the reducer works, R1–R6, BASE/OFFSET/COMMAND) and `optimal-lambda-reduction` (implementation roadmap, paper2 efficiency caveats). The ontology layer **depends** on the reducer, so read the bus skill before touching ontology code.

## The one idea everything follows from

Typing here is **not** ordinary typed lambda calculus. Types **are** entities of the knowledge graph, and the type of the term answers one question: **where is the reader standing?**

```
[a]         = A        for a variable a of type A
[app(a,b)]  = [b]      <-- the load-bearing rule
[(lam a) t] = [a]      the BOUND variable's type, not the body's
```

Because the type rides on the **argument**, extending a term along `A -> B` moves the reader to `B`, while wrapping a term in a new *function* position leaves the reader where it was. Every design decision below is a consequence.

Normative implementation: `term_utils._term_type` implements exactly these three rules. Treat it as ground truth; verify claims against it rather than reasoning in your head (that is how the case-3/case-4 collapse and the case-5 redundancy were both established).

## Reading = (G(t), P)

`t` is a lambda term (the record of the path), `G(t)` its sharing graph, `P` a set of **pointers** to nodes/edges — the reader's live positions. The reader is never outside the reading; `|P| > 1` means attention has forked and not yet collapsed.

## The operations

Contraction has **three** primitive cases — 1, 2, 4 — plus **reflection**. Numbering is preserved from earlier drafts; cases 3 and 5 were removed (below).

| | argument position | function position |
|---|---|---|
| **fresh variable** | **case 1** (depart `A->B`) | *collapses into case 4* |
| **saved reading** | **case 2** (descend into `R1`) | **case 4** (arrive from `R1`) |

**The governing principle:** *the argument determines both the resulting type and the surviving pointers.* Argument position → the reader moves into the material and adopts its pointers. Function position → the reader stays put and the material becomes context behind it.

- **case 1** `A->B`, requires `[t1]==A` → `app(t1,b)`, type becomes `B`, `p` re-pointed.
- **case 2** `A->R1`, requires `[t1]==A` → `app(t1,R1)`, type becomes `[tr1]`, pointers `Pr ∪ (P−p)` — `p` is *consumed*, the reader **descends** into `R1`.
- **case 4** `R1->B`, requires `[t1]==B` → `app(tr1,t1)`, type **unchanged**, `Pr` **discarded**, `p` re-pointed.
- **reflection** → `app(app(tA,b), app(tA,c))` with `tA` **shared by a fan-in**; `p` becomes `{pB,pC}` on the fan-in's `grey`/`black` aux ports. The only operation that shares, and the only one that increases `|P|`.

Every operation acts at exactly **one** pointer and leaves all others untouched. No exceptions.

## Why case 3 and case 5 are gone (both verified, don't re-litigate)

**Case 3** ("arrive along `A->B`", prepend a fresh variable) is an *exact* special case of case 4 at a trivial single-variable reading: same term `app(a,t1)`, same precondition `[t1]==B`, same type, same pointers. The collapse works **because case 4 discards `Pr`** — the trivial reading's pointer contributes nothing.

**Case 1 is NOT similarly reducible to case 2**, and the asymmetry is instructive: a fresh variable differs from a saved reading only in having no pointers. In function position pointers are discarded anyway, so the distinction is invisible; in argument position they are adopted, so it is load-bearing. That is the hole in the upper-right cell of the table.

**Case 5** ("join two readings", `app(t1,t2)` keeping `P2`) was removed to make every step local. It was **redundant, not load-bearing** — cases 2/4 already graft a saved reading's *whole term* at a pointer, and a saved reading may itself be arbitrarily compound. Both cases are retained in the notes as future enhancements with their open questions preserved.

## The epistemic design principle (§3b) — the real invariant

**The reader does not know the knowledge graph in advance.** Every operand must be something it already knows:

- **(K1) local visibility** — standing at `A`, it sees only arrows *incident* to `A`. It cannot name an entity two hops away.
- **(K2) its own readings** — a saved reading is its own prior work, however far it ranges.

So "locality" is not a rule in its own right; it is what (K1) **forces** when the graph is unknown. (K2) enters only *through* a pointer (cases 2/4 graft at the position the reader occupies).

**Consequences that matter when extending this:**

- A general "navigate to an arbitrary position/entity" step is **inadmissible** — it presupposes exactly the knowledge (K1) denies. Do not add one, even though it would make cases 1+4 shape-complete on their own. It is also unnecessary (see below).
- This explains why case 2 adopts `Pr` and case 4 discards it: entering a reading means inheriting positions you have yourself stood at; a reading *behind* you is context, not a place you occupy.
- **init-1 is the single admitted breach** — picking a first entity out of the blue. That presumably corresponds to search/browse, outside the calculus. Everything after is (K1) or (K2).

## Verified expressiveness results (§10)

Established by exhaustive enumeration to 6 leaves. Scope: applicative terms `t ::= x | (t t)`, no abstractions, plus sharing.

- **Application shapes: COMPLETE** with cases 1, 2, 4 alone (Catalan 1,1,2,5,14,42 — none missing), *without* case 5. What does the work is that cases 2/4 graft a saved reading's entire term at a pointer.
- Cases 1+4 restricted to **fresh variables only** are *incomplete* — first failure `((x y) (z w))` at 4 leaves (missing 1/5, 6/14, 26/42). The obstruction is **pointer reachability**, not the rewrite rules: fresh-variable grafting never *adds* a pointer, so from init-1 the reader extends along a single spine. Confirmed by re-running with a hypothetical pointer at every node → complete at every size.
- **Consequence: saving a reading is load-bearing**, not a convenience — it is what makes the calculus shape-complete.
- **Sharing: INCOMPLETE.** Reflection emits one fixed pattern, so a shared node always has exactly two parents, both APP, with `tA` in *function* position of both. Not constructible: fan-out > 2; **sharing in argument position** (the sharpest gap — exactly what optimal reduction exists to exploit); asymmetric/late sharing; cycles. Candidate closure is a **merge/coreference** step (dual of reflection: consume two pointers, yield one) — proposed only, see O9.

## Ontology (§7) — reading as observation, ontology as theory

| | |
|---|---|
| **reading** | the observation — bare applicative term, **no** abstractions |
| **ontology** | the theory — a term **with** abstractions whose reduction replays the reading |
| **reducer** | the test — the rule must actually fire |

The correspondence is exact: **contraction ↔ R1** (fan/fan same wire, annihilate — the β-step); **reflection ↔ R4** (fan/fan different wires, duplicate). Notation `ont(G(t), P)`, with `P` corresponding elementwise to the reading's pointers. Each reading carries a **set** of ontologies = every hypothesis still consistent with it.

**Questions** are the source of abstractions, and they are **already implemented**: the user builds a reading, presses `a` ("Ask question"), selects the *queried entity* →

```
app.py: action_lambda_abstraction  ->  widgets.LambdaAbstractionModal
    abs_term = LamAbs(var=selected_entity, body=reading_term)
    persisted by term_utils.append_lambda_term
    looked up by widgets._record_contains_iri:  term.var.iri == iri
```

Since `[(lam a) t] = [a]`, that lookup **is** "all questions of type A" — a finite existing query. A question is *a reading turned into a schema* by making one entity a parameter; substituting one during enrichment hypothesises "this position is an instance of that earlier generalised reading."

**Lifecycle per step: UPDATE (filter + advance), then ENRICH.**

- **Enrichment** substitutes questions for variables sitting in applications, branching over `QUESTIONS(type)` at each pointer. **Terminates** because recursion is strictly on the shrinking pointer set `P − p`; a question is never substituted inside a question introduced in the same enrichment. Note the asymmetry: substituting in *function* position replaces `p` by a pointer to the question; in *argument* position `P` is unchanged (same argument/function principle).
- **Update** applies R1 (contraction) or R4 (reflection) plus **all** bookkeeping (R2/R3/R5/R6), then advances pointers in step with the reading.
- **Refutation:** the set is *replaced*, so any ontology where the rule does not fire is **discarded** — falsified by what the user did. **An empty set is meaningful**: the reading has no valid model. Surface it to the user; deliberately *not* an assertion failure.

### Two traps specific to the ontology layer

1. **Reflection's sharing test is a precondition, by decision** — `G(toa)` must be *already* shared across both branches. This is **stricter than the reducer's own test**: `rules.py:rule4_fan_fan_diff` fires whenever two fans meet principal-to-principal on different main wires, and its *effect* is to replicate each onto the other's grey/black branches — the reducer **creates** duplication rather than requiring it. So `IS_SHARED_IN_BOTH` is an extra filter layered on `find_redexes` and must be implemented separately, not read off redex detection.

2. **Pointer stability under reduction is a hard blocker, not hypothetical.** Ontology pointers are held *across* R1/R4 and bookkeeping, and `optimal_lambda/rules.py` knows nothing about pointers — `_annihilate`, `_capture_ports`, `_finalize` rewire ends freely. Either extend the rules to carry pointers through, or re-derive them after each reduction from something stable (e.g. `SyntaxRole`/`iri` on fans). **This must be solved before the ontology layer can run at all.** (The reading layer itself never reduces — only the ontology layer does.)

## Open points to respect

Full list in `notes/reading_desc` §9. The ones that will bite:

- **O1** copy vs. share when grafting a saved reading (flag `SHARE_GRAFTED_READING`, defaulted to copy).
- **O2/O3** reflection's `A`/`B`/`C` relation and whether its operands are fresh variables or entity constants — both *reconstructed*, the least certain part of the spec.
- **O4** saving persists `(name, term)` only — neither pointer sets nor ontology sets — so init-2 and case 2 cannot be restored across sessions. Now load-bearing, not incidental.
- **O10** ontology update for cases 2/4 — left open by decision (candidate: cartesian product of the two ontology sets).
- **O11** ontology-set size bound: enrichment branches multiplicatively, refutation prunes, nothing guarantees the rates balance.
- **O12** the reading↔ontology pointer correspondence, on which every update rule depends totally.

## Working conventions for this material

- **Verify against code, don't reason in your head.** `term_utils._term_type` for typing claims, `optimal_lambda/rules.py` for rule behaviour, exhaustive enumeration for expressiveness claims. Two of this session's conclusions were corrected exactly this way.
- **Marked provenance:** `[ASSUMPTION]` = reconstructed to make the definition total; `[CLARIFIED]` = settled by Marek in discussion; `C1` = explicitly confirmed. Preserve these markers.
- **Keep the numbering** (cases 1, 2, 4) so earlier notes still refer to the same cases. Retired items stay as omission/removal notes with their rationale, not deletions.
- Vocabulary: reading · pointer · contraction (depart/descend/arrive) · reflection · question · ontology · enrichment · refutation · (K1)/(K2) · trivial reading · argument/function position.
