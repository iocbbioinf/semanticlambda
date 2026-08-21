---
name: ontology
description: The Ontology layer of the Reading calculus — ont(G(t), P), the set of models in which a reading is valid, maintained by enrichment (substituting the positive form of abstractions) and refutation (reduction must fire, R1 for contraction and R4 for reflection). Covers the update rules, pointer stability under reduction, the abstraction title-vs-positive-form distinction, and the empty-set signal. Use when reading, explaining, extending, implementing or debugging anything about ontologies, enrichment, refutation, questions/abstractions as ontology material, or how reading steps drive graph reduction. For the reading calculus itself use `reading`; for what it all MEANS use `reading-interpretation`.
---

# The Ontology Layer

An **ontology is a model in which a reading is valid**. Each reading carries a
**set** of them — every hypothesis still consistent with what the user has done.

**Authoritative specification — read it, do not work from this summary alone:**

- `notes/reading_desc` **§8** — the definition: what an ontology is, vocabulary,
  initialisation, enrichment, pointer stability, both update rules (§8.6
  contraction, §8.7 reflection), refutation.
- `notes/reading_desc` §1–§7 — the reading calculus this layer is bound to.
- `notes/reading_alg` §5–§8 — pseudocode: the shape, the reduction discipline
  (silence/approval), enrichment, both updates, refutation, and the driver that
  keeps the set in step with the reading.

Sibling skills: **`reading`** (the formalism this layer sits on top of),
**`reading-interpretation`** (what it all *means* — **read this before touching
§8**, it is what §8 formalises), `understand-bus-optimal-reduction` (R1–R6,
BASE/OFFSET/COMMAND — the rules this layer actually fires),
`optimal-lambda-reduction` (implementation roadmap, efficiency caveats).

`[OPEN]` markers in §8 are load-bearing — preserve them.

**IMPLEMENTED** in `ontology_state.py`, driven from `app.py`
(`_ont_after_contraction` / `_ont_after_reflection`), surfaced by `o` →
`ReadingOntologiesModal`. Note it works on **terms, not the bus graph**: rule 1
applies exactly where an abstraction meets an application, firing it is beta at
that position, and silent reductions have no term-level effect — so
`optimal_lambda.normalize` is never called. That seam is documented at the top of
`ontology_state.py`; revisit it if the layer ever needs context semantics.

⚠️ **O9:** the bare init ontology is refuted by the FIRST contraction (the outer
test needs an application it does not have), and enrichment cannot help at init
either (both its cases need an enclosing application). So the set is non-trivially
populated only from the *second* contraction on. **Unruled** — see O9, which traces
it to an ambiguity in *when* §8.6's outer test is read.

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

**THE READING IS A MEMBER OF ITS OWN SET** (§8.1b, `reading_ontology`). It
qualifies trivially — it contains exactly the material the user built — but it
**never fires**, since a reading has no abstractions (I6). So it is the degenerate
model: never contradicted, never explanatory, `fired` always 0.

It is maintained by **mirroring** the reading after each step, NOT by §8.6/§8.7 —
those test a *candidate* against a step, and this member *is* the step. Keep it out
of the update rules (`strip_reading_ontology` before, `sync_reading_ontology`
after). Consequence: the set is never empty while a reading exists, so **the §8.8
signal is "no candidate besides the reading"**, not "empty".

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
- **It covers the abstraction-title operand.** When the reading contracts a
  *title*, the ontology side needs no rule of its own — the title's counterpart is
  that abstraction's positive form, and enrichment substitutes it on the same step.
  ⚠️ A **named** title collapses the quantifier: substitute *that* abstraction, do
  not branch over all of matching type.

**Update** (§8.6–8.7) — advance pointers in step with the reading. Contraction
**forks** rather than filters:

- **R1 fires** — reduce (plus all silent reductions), advance the pointer. The
  ontology's hypothesis paid off.
- **R1 does not fire** — **keep the ontology anyway**, just advance the pointer. It
  proposed nothing at this position; that is *silence*, not contradiction.

**Only option 1 takes a closed-reading operand** (option 2 is entity-only, §4.1):
for each of `R1`'s own ontologies, substitute its term for the variable at that
position and require R1 to fire — a cartesian product over the two sets.

**Reflection (§8.7) also forks**:

- **`toc` is an abstraction** and R4 applies → **reduce**; the two new pointers
  designate **nodes** among the four fans R4 creates.
- **`toc` is not an abstraction** but a **sharing fan-in is already above it** (on
  the different, more-left wire) → **nothing reduces**; `G(t)` is unchanged and the
  two new pointers designate the **edges** at the existing fan-in's top ports.

Both branches require the sharing to be **already present**, so an ontology with no
fan-in above `toc` fails both and is **refuted**. Reflection therefore refutes
*harder* than contraction, whose non-firing branch demands no structure of its own.

## Pointer stability under reduction — SOLVED (§8.5)

This was the blocker for the whole layer. It is resolved, and the resolution is
the most useful thing in §8.

1. **Under enrichment** — if `p → a` and `a` is replaced by positive form `lamA`
   with `[a] == [lamA]`, the new pointer set holds a pointer to `lamA`. **The
   pointer follows the material.**
2. **Only two pointered configurations** — a pointer designates an abstraction,
   and immediately above that edge is either a fan-in on the **same** wire (→
   **R1**) or on a **different, more-left** wire, the sharing node (→ **R4**).
3. **Which reductions are silent — a STRUCTURAL test on the marked wire**
   (it is *not* about where the pointer is):

   | | silent? |
   |---|---|
   | **R2, R3, R5, R6** | **always** — delimiter shuffles, no syntactic content |
   | **R1** | iff on a **non-rightmost** wire. On the rightmost wire the fans are the syntactic **λ and @** → a real **beta-step**, NOT silent |
   | **R4** | iff the fan-out is **not** rightmost (a **sharing** node). A **rightmost** fan-out is an **abstraction** → the **reflection**, NOT silent |

   **Every non-silent reduction must be approved by the user during reading.** That
   is the entire coupling between the layers: rightmost-wire R1 *is* a contraction
   the user made (§8.6); rightmost-fan-out R4 *is* a reflection they made (§8.7).
   The ontology layer never fires either on its own initiative — which is what makes
   it a *model of that reading* rather than an independent computation. A silent
   cascade therefore can never run ahead of the reading.

   **Why "rightmost" is the right test** — verified in code: a fan is syntactic
   exactly when `main == width - 1`. `compile.py` builds λ/@ as `new_fan(2, 1, …)`
   (width 2, main 1 → rightmost), while `_fan_in` builds sharing as
   `new_fan(INITIAL_ROOT_WIDTH, OFFSET, …)` (width 3, main 1, rightmost is 2). Same
   reason read-back follows the COMMAND/rightmost wire and branches only at
   syntactic fans.

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
applicable" and "`G(toc)` is already shared in both branches" are **independent**
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

**The OUTER STRUCTURAL TEST is the strong gate.** It demands the ontology **already contain the material the
reading is about to build** — an `app(ta,tb)` at the pointer with `[tb]` the
contraction's target type. Since enrichment substitutes abstractions but **never
adds applications**, an ontology passes only if some abstraction *body* supplies
that argument position. Passing it every step is demanding, not a formality.

**Worked counterexample** (RN1 in §8.6): reading `(a b)`, contracting `B -> C`.
With abstractions `(lam a).(a d)`, `(lam b).(b e)`, `(lam c).(c f)`, the candidate
`((lam b).(b e)) d` models `(a b)` but at the `B -> C` step the pointer designates
the *whole* term, which has no enclosing application — outer test fails, **refuted**.
No body supplies a `c`, so all candidates die and the set goes **empty**.

**What the non-firing branch actually costs** — a narrow leak. It matters only for
ontologies that *already pass* the outer test: those have the right shape, and
previously also needed `ta` to be an abstraction. Now they don't, so the rule admits
ontologies with the right **shape** but no **hypothesis** at the pointer. Real but
narrow, and keeping them is right — silence isn't contradiction.

**O8** (unruled): whether to count firings per ontology so that "survivors are
better approximations" tracks *confirmation* rather than mere survival. A ranking
refinement, not a fix for a hole.

**A set holding only the reading is MEANINGFUL:** no available explicit material
accounts for what this user is doing — the reading has outrun the abstractions
available to it. **Surface it; never make it an assertion failure.** (The set itself
is never empty while a reading exists, §8.1b.)

This is the formal counterpart of the interpretation's *"only instances that fit
the rule remain, and survivors are better approximations"*. The set shrinks by
refutation and grows by enrichment; as reading continues and an ontology still
fits, the approximation improves.

## Open points

- **Closed readings carry their ontology set.** A closed reading **carries its ontology set**: ontologies are
  stored on save, and closing drops only the pointer set (§1). Re-deriving by
  enrichment was rejected — the stored ontologies are *survivors* of refutation over
  that reading's whole construction, which enrichment cannot recover, proposing
  candidates by type alone. **Implementation prerequisite:**
  `kg_store.save_readings_db` holds `(name, term)` only and must be extended.
- **O8 — RESOLVED.** All three operands are covered: **entity** (§8.6); **closed
  reading** (§8.6, option 1 only — pair each current ontology with each of `R1`'s,
  substitute, require R1 to fire); **abstraction title** (no case needed — enrichment
  substitutes that title's own positive form, §8.4). Option 2 needs no
  closed-reading case, being entity-only by intent (§4.1) — which is also why only
  option 1 ever had a combining problem: its operand occupies a position the current
  ontologies say nothing about, whereas option 2's would land in function position
  exactly where the R1 redex is.
- **O7** — whether a size bound is needed. Enrichment branches per pointer per
  step and option 1's closed-reading case multiplies by `|R1.ontologies|` (RN4);
  against that, the outer test refutes heavily. Whether the rates balance is
  unsettled.
- **O8** — whether to record confirmation counts, so survival tracks confirmation (above).

## Review notes on §8.6 (unruled)

§8.6 carries five marked review notes (numbered **RN1–RN5** to avoid collision
with the reducer's rules R1–R6). Read them
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
