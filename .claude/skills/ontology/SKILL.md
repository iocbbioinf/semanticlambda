---
name: ontology
description: The Ontology layer of the Reading calculus — ont(G(t), P), the set of models in which a reading is valid, maintained by enrichment (substituting the positive form of abstractions) and refutation (reduction must fire, R1 for contraction and R4 for reflection). Covers the update rules, pointer stability under reduction, the abstraction title-vs-positive-form distinction, and the empty-set signal. Use when reading, explaining, extending, implementing or debugging anything about ontologies, enrichment, refutation, questions/abstractions as ontology material, or how reading steps drive graph reduction. For the reading calculus itself use `reading`; for what it all MEANS use `reading-interpretation`.
---

# The Ontology Layer

An **ontology is a model in which a reading is valid**. Each reading carries a
**set** of them — every hypothesis still consistent with what the user has done.

**Authoritative specification — read it, do not work from this summary alone:**

- `notes/reading_desc` **§8** — the definition: what an ontology is, vocabulary,
  initialisation, enrichment, pointer stability, both update rules, refutation.
- `notes/reading_desc` §1–§7 — the reading calculus this layer is bound to.

Sibling skills: **`reading`** (the formalism this layer sits on top of),
**`reading-interpretation`** (what it all *means* — **read this before touching
§8**, it is what §8 formalises), `understand-bus-optimal-reduction` (R1–R6,
BASE/OFFSET/COMMAND — the rules this layer actually fires),
`optimal-lambda-reduction` (implementation roadmap, efficiency caveats).

Definition given by Marek 2026-08-14. Provenance markers `[CLARIFIED]` /
`[OPEN]` in §8 are load-bearing — preserve them.

## The shape of it

```
ont( G(t), P )
```

A shared graph representing a term, plus a set of pointers to **edges** — the
places the user is standing **in this graph**. `P` corresponds **elementwise** to
the reading's own pointer set: every reading pointer has a counterpart in every
ontology.

**Validity means reduction replays the reading:**

| in the reading | in the ontology |
|---|---|
| contraction | **R1** fires (fan/fan same wire — annihilate) |
| reflection | **R4** fires (fan/fan different wires — duplicate) |

**The reading never reduces. Only this layer does.** That asymmetry is the whole
reason pointer stability (below) is a problem here and nowhere else.

## Reading vs ontology: opposite sides of the visibility line

From `reading-interpretation`: an abstraction has two sides, and the layers see
different ones.

| side | expresses | who sees it |
|---|---|---|
| **title** | the *breaking* — loss of sense (**negative** form) | the **reading** — contracted just like an entity |
| **positive form** `G(lam a.t)` | the *form/context* of the breaking | **ontologies only** — invisible to a reading |

So a reading is subjective and abstraction-free (invariant I6); an ontology is the
**explicit** layer, built from positive forms. Reduction is the **explicit act of
answering** the question — in the calculus rather than in the user's mind.

**Abstracting retypes**, and this trips people up:

```
[ G(lam a.t) ] == [a]        which DIFFERS from [t]
```

"All abstractions of type A" therefore tests the **bound variable**, not the body
— a finite concrete lookup, already what `widgets._record_contains_iri` does
(`term.var.iri == iri`).

## Lifecycle: every reading step is UPDATE then ENRICH

**Enrichment** (§8.4) — wherever a pointed subterm is a **variable in an
application**, substitute the positive form of an abstraction of matching type,
yielding another candidate. Grows the set.

- **Iterate a SNAPSHOT.** `enrichOnts` walks the set while `enrichOnt` adds to it;
  without a snapshot, new ontologies get re-enriched in the same pass and it need
  not terminate.
- **Terminates** because recursion is strictly on the shrinking `P` — a newly
  created ontology is enriched only for the *remaining* pointers, so an
  abstraction is never substituted inside one introduced in the same pass. Depth
  bounded by `|P|`.
- **Asymmetric by design**, following `[app(a,b)] = [b]`: substituting in
  **function** position makes the abstraction a new position (`p` → `pqp`); in
  **argument** position `P` is unchanged.

**Update** (§8.6–8.7) — advance pointers in step with the reading. Contraction now
**forks** rather than filters (redefined 2026-08-18):

- **R1 fires** — reduce (plus all silent reductions), advance the pointer. The
  ontology's hypothesis paid off.
- **R1 does not fire** — **keep the ontology anyway**, just advance the pointer. It
  proposed nothing at this position; that is *silence*, not contradiction.

Option 1 also handles a **closed-reading** operand: for each of `R1`'s own
ontologies, substitute its term for the variable at that position and require R1 to
fire — a cartesian product over the two sets.

## Pointer stability under reduction — SOLVED (§8.5)

This was the blocker for the whole layer. It is resolved, and the resolution is
the most useful thing in §8.

1. **Under enrichment** — if `p → a` and `a` is replaced by positive form `lamA`
   with `[a] == [lamA]`, the new pointer set holds a pointer to `lamA`. **The
   pointer follows the material.**
2. **Only two pointered configurations** — a pointer designates an abstraction,
   and immediately above that edge is either a fan-in on the **same** wire (→
   **R1**) or on a **different, more-left** wire, the sharing node (→ **R4**).
3. **Everything else reduces silently.** ⚠️ **The criterion is the ABSENCE OF A
   POINTER, not the rule number.** R2/R3/R5/R6 are always silent, but **R1 and R4
   are silent too when no pointer of `P` is involved**. "Bookkeeping = rules
   2,3,5,6" is the common case, not the definition.

**Where the two pointers land after R4** — verified by tracing a real firing, not
inferred: `rule4_fan_fan_diff` **deletes both fans and creates four**. Two are
copies of the **abstraction's** fan (replicated onto the sharing fan's grey/black
branches), carrying the original's `role` and `iri`; their principals reach what
were the sharing fan's externals. **Those two** are the left/right nodes the new
pointers designate. The other two are copies of the *sharing* fan — not pointer
targets.

**No cascade risk** — a step acting at one pointer cannot disturb another. R4
removes only the two fans that met and re-attaches all captured branch externals
(`_capture_ports` / `_finalize`). Confirmed by trace: no new redex at the
untouched anchors. So the elementwise correspondence with the reading survives
every step.

## Two traps in the update rules

**1. Reflection's already-shared test is a SEPARATE, STRICTER filter.** "R4 is
applicable" and "`G(toa)` is already shared in both branches" are **independent**
conditions. `rules.py:rule4_fan_fan_diff` fires whenever two fans meet
principal-to-principal on different main wires, and its *effect* is to replicate
each onto the other's branches — **the reducer CREATES duplication rather than
requiring it**. So the sharing test must be implemented separately, never read off
`find_redexes`. It is a precondition *by decision*, and it matches the
interpretation: reflection reflects from *within*, so the sharing pre-exists.

**2. Contraction option 2: the pointer and the redex are in different places.**
Option 2's pointer designates `tb`, the **argument**, but the abstraction — hence
the **R1 redex** — is at `ta`. Test applicability **at `ta`, not at the pointer**;
only the surviving pointer derives from `p`. (Option 1 is the case where the two
coincide.) Also: option 2 carries `P` over unchanged because the reader does not
move, but the pointers must be **rebased** onto the rewritten graph — "unchanged"
means the same positions, not the same edge identities.

## Refutation, and the empty set

Updates **replace** the set, so **an ontology is discarded by never being added** —
there is no discard statement, and refutation's strength is exactly the strictness
of the update rules' conditions.

⚠️ **Since the 2026-08-18 redefinition, contraction refutes almost nothing.** §8.6
now **keeps** an ontology in which R1 does *not* fire — distinguishing
**contradicted** from merely **silent**, which is right in itself. But that was the
gate that did the discarding, so the only one left is the outer structural test,
which most enriched ontologies pass by construction. The set grows nearly
monotonically.

Two consequences to know before implementing:

- **O10's cap is now a prerequisite**, not an optimisation — nothing balances
  enrichment's growth.
- The interpretation's claim that survivors are **better approximations** because
  they "still fit" is weakened: if silence counts as fitting, survival no longer
  tracks confirmation. **O11** proposes counting firings per ontology and evicting
  by that count, making refutation a *ranking* rather than a filter. Unruled.

**An empty set is MEANINGFUL:** no available explicit material can express what
this user is doing — the reading has outrun the abstractions available to it.
**Surface it to the user; never make it an assertion failure.**

This is the formal counterpart of the interpretation's *"only instances that fit
the rule remain, and survivors are better approximations"*. The set shrinks by
refutation and grows by enrichment; as reading continues and an ontology still
fits, the approximation improves.

## Open points

- **O9 — the reflection update rule is STALE.** ⚠️ Reflection was redefined
  2026-08-15: the reading now builds `app(t,t)` with the branch types **cast onto
  the two occurrences** of one shared `t`, emitting no fresh variables. §8.7 still
  tests for a subtree `app(tb,tc)` with the types on separate operands — a shape
  the reading no longer produces. **The rule must be restated against the new
  form before it can be implemented.** Everything else in §8 (enrichment,
  pointer stability, refutation, the contraction rules) is unaffected.
- **O7** — whether a closed reading carries its ontology set. §8.3 form (b) takes
  "the ontologies of R1", but a closed reading is the graph alone (§1). Either
  closing persists them or re-opening re-derives them by enrichment. §8.6's
  closed-reading case **needs** them, so this must be settled.
- **O8 — partly resolved.** Option 1 now handles a closed-reading operand: pair
  each current ontology with each of R1's, substitute R1's ontology-term for the
  variable, require R1 to fire. Still open: whether **option 2** admits a closed
  reading at all (no case is given, so it currently means entity-only); the
  **abstraction title** operand (no case in either option — plausibly needs none,
  being covered by enrichment substituting that title's own positive form, *without*
  branching over all abstractions of the type, since the user named one); and
  review notes **RN2/RN3/RN5** in §8.6.
- **O10 — now a prerequisite.** Cap plus deterministic eviction. Enrichment
  branches per pointer per step, option 1's closed-reading case multiplies by
  `|R1.ontologies|`, and refutation no longer counterbalances.
- **O11** — whether non-firing survival needs a confirmation count (above).

## Review notes on §8.6 (unruled)

§8.6 carries five marked review notes (numbered **RN1–RN5** to avoid collision
with the reducer's rules R1–R6) from the 2026-08-18 definition. Read them
before implementing that section: **RN1** non-firing survival vs refutation (above),
**RN2** no fallthrough when `tb` is neither a variable nor one of R1's
ontology-terms, **RN3** the "`tb` is an ontology of R1" test compares different
graphs and needs restating, **RN4** the closed-reading case is a cartesian product,
**RN5** the resulting pointer must designate the *post-reduction* material.

## Working conventions

- **Verify against code, not intuition.** `rules.py` for rule behaviour,
  `term_utils._term_type` for typing, an actual traced firing for anything about
  what a rule does to nodes. Both of §8's verified claims (R4 pointer targets, no
  cascade) were established this way — and the already-shared trap was found by
  reading `rule4_fan_fan_diff` rather than trusting its name.
- **`ont(G(t), P)` mirrors a reading deliberately.** Keep the elementwise pointer
  correspondence; every update rule depends on it totally.
- Vocabulary: ontology · `ont(G(t),P)` · enrichment · refutation · title (negative
  form) · positive form · silent reduction · pointered configuration ·
  approximation.
