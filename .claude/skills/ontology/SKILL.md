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

So a reading is subjective and abstraction-free (invariant I7); an ontology is the
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

**Update** (§8.6–8.7) — apply R1 or R4 plus all silent reductions, and advance
pointers in step with the reading. Shrinks the set by refutation.

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

Updates **replace** the set. An ontology in which the required rule does not fire
is **discarded** — falsified by what the user just did. Two silent ways to be
dropped: failing the outer structural test, and failing applicability. Neither is
an error.

**An empty set is MEANINGFUL:** no available explicit material can express what
this user is doing — the reading has outrun the abstractions available to it.
**Surface it to the user; never make it an assertion failure.**

This is the formal counterpart of the interpretation's *"only instances that fit
the rule remain, and survivors are better approximations"*. The set shrinks by
refutation and grows by enrichment; as reading continues and an ontology still
fits, the approximation improves.

## Open points

- **O8** — whether a closed reading carries its ontology set. §8.3 form (b) takes
  "the ontologies of R1", but a closed reading is the graph alone (§1). Either
  closing persists them or re-opening re-derives them by enrichment.
- **O9** — the update when the contraction operand is a **closed reading or an
  abstraction title** rather than an entity. Both carry their own ontology sets,
  to be combined somehow. **Load-bearing**: operand grafting is what makes the
  calculus shape-complete (§7.1).
- **O10** — whether the set needs a size bound. Enrichment terminates but
  branches over every matching abstraction at every pointer on every step;
  refutation prunes, but nothing guarantees the rates balance.

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
