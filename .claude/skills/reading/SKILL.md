---
name: reading
description: The Reading calculus — this project's model of a user reading a knowledge graph as a typed applicative lambda term built bottom-up in a sharing graph. Covers the one contraction step (two options), the reflection step and its type cast on shared occurrences, typing-as-location, and verified expressiveness results including how argument-position sharing is constructed. Use when reading, explaining, extending, implementing or debugging anything about readings, pointers, contraction, reflection, questions, open vs closed readings, or sharing.
---

# The Reading Calculus

This project models **a user reading a knowledge graph** as the incremental,
bottom-up construction of a typed applicative lambda term held as a sharing
graph.

**Authoritative specification, do not duplicate — read these files:**

- `notes/reading_desc` — the **normative** prose spec (§1–§10): what a reading is,
  typing, the steps, invariants, expressiveness, **§8 ontology**, worked examples,
  open points.
- `notes/reading_alg` — pseudocode implementing it end to end: data structures,
  typing at an occurrence, both steps, the argument-sharing construction, the
  **silence/approval boundary**, ontology maintenance, the **driver**, invariant
  check, implementer notes. Where the two disagree, `reading_desc` wins.

**The notes state the current concepts only — they do not track their history.**
If you recall cases numbered 1/2/4/5, pointer adoption from grafted readings, or
an ontology layer built on those, that material is gone; do not reintroduce it.
`reading_alg` is where to look for the driver loop and the silence criterion,
which the prose leaves distributed across §4, §8.5 and §8.6.

Sibling skills: **`reading-interpretation`** (what the operations MEAN — sense
movement, the two sides of abstraction, subjectivity, why GAL locality matters;
**load it for any "why is it this way" question, and before changing an
operation**), **`ontology`** (the §8 ontology layer — `ont(G(t),P)`, enrichment,
refutation, pointer stability), `understand-bus-optimal-reduction` (how the
reducer works, R1–R6, BASE/OFFSET/COMMAND) and `optimal-lambda-reduction`
(implementation roadmap, paper2 efficiency caveats).

## The one idea everything follows from

Typing here is **not** ordinary typed lambda calculus. Types **are** entities of
the knowledge graph, and the type answers one question: **where is the user
standing?**

```
[a]         = A        for a variable a of type A
[app(a,b)]  = [b]      <-- the load-bearing rule
[(lam a) t] = [a]      the BOUND variable's type
```

Because the type rides on the **argument**: material in argument position moves
the type to it; material in function position leaves the type untouched.

Normative implementation: `term_utils._term_type`. Verify typing claims against
it rather than reasoning in your head.

## A reading is a process

**OPEN** while being created: `R = (G(t), Pr)` — a sharing graph plus a set of
pointers to **edges**. **CLOSED** once saved: `(G(t), ontologies)` — the graph and
its ontology set. **The pointer set is the only thing closing drops.**

Before each step the user selects one pointer, **actPtr** — *the place where the
user stays*. Pointers exist only because of reflection; contraction never
changes their number.

**The two states are not two kinds of object.** A closed reading is *de facto
equivalent to a reading with one pointer, to the root of its graph* — closing
**collapses** the pointer set to the root. Hence: saving is **total** (closable
from any state), and re-opening is well defined for every closed reading. *Scope:*
this says what a closed reading **is**, not that the two are interchangeable
everywhere — §4's steps act on an open reading, so a closed one must be re-opened
before being stepped on.

⚠️ **Ontologies ARE stored on save** — they are survivors of refutation over that
reading's whole construction, which enrichment cannot re-derive since it proposes
candidates by type alone. Pointers need no storing (the root one is re-minted), but
the ontology set does: `save_readings_db` takes it as a second argument and
`load_readings_ontologies` reads it back.

**Opening** (§3), two forms: a **type** A → `(G(a), {ptr to a})`, or an **already
saved reading** → `(G(t), {ptr to G(t)})`. Both start with exactly one pointer.

**`Pr` never shrinks.** No operation removes a pointer — contraction re-points
actPtr, reflection replaces it with two. So `Pr` grows monotonically and is never
empty.

A closed reading contributes only its **type** and graph when used later. This
is why **option 1** treats entities and closed readings alike: **there is never a
pointer set to adopt or discard.**

## The steps

**Contraction** — one case, two options. Relates **two places**: the selected
operand and where the user stays (actPtr).

**Both options build `app(t1, t2)` with `[t1]==A` and `[t2]==B`.** They differ only
in *which one actPtr designates* — so **A and B name positions in the term, not the
option**:

| | actPtr at | operand | builds | type after | reader |
|---|---|---|---|---|---|
| **option 1** | `t1` [A] | `t2` [B] — entity **or closed reading** | `app(t1,t2)` | **B** | **moves** to B |
| **option 2** | `t2` [B] | `t1` [A] — **entity only** | `app(t1,t2)` | **B** (unchanged) | **stays** at B |

**Both read the same way: A asks, B answers.** The asymmetry is not in the question
but in where the user stood when it was put. Option 2's unchanged type is not a
failure to act — the act was putting the question; the answer is where the user
already was.

⚠️ **Don't swap those letters.** Writing option 2 as `app(t2,t1)` makes §8.6's
ontology-side type test (`[ta]==A and [tb]==B`, stated once for both options)
unsatisfiable, refuting every ontology at every option-2 step.

**The options are not duals** — only option 1 accepts a closed reading (intended,
not an omission): a closed reading may be what the user answers **with**, never
what they ask **from**. Costs nothing formally — §7.1 stays complete, and §7.2 uses
option 2 only with fresh entities — and it removes the harder half of the
ontology-side combining problem, since option 2's operand would land in function
position exactly where the R1 redex is.

**Reflection** — user stays at C, selects `A -> B`, and reflects A as question
against B as answer without moving to either:

```
G(t)  |->  app(t, t)      with t SHARED by a new fan-in
```

**No fresh variables are emitted** — both branches *are* the shared subject.

`actPtr` becomes two pointers on the fan-in's **left-up/right-up aux ports** —
`grey`/`black` in `_Compiler._fan_in`. **They designate the aux-port edges, i.e.
the two occurrences of `t`, not the enclosing app node.** This is the single most
consequential detail in the spec (see sharing, below).

**Reflection CASTS the type.** The left-up edge is type **A**, the right-up edge
type **B**, though both are one shared `t` whose own type was C. This
**deliberately breaks §2's typing** — not `[app(a,b)]=[b]`, but the assumption
that a *subgraph has a type at all*:

- type at a pointer is well defined, as a property of the **occurrence** (I3);
- `[t]` alone is no longer a function of the term, so **`term_utils._term_type`
  is insufficient** for reflected material — it needs an occurrence argument (O1);
- contraction's `[t1]==A` must be read **at the pointer's occurrence**.

This matches GAL's own geometry: direction there is likewise recovered
per-traversal, and the two occurrences are distinguished by the same grey/black
branch that distinguishes their contexts.

**One kind of sharing: the explicit fan-in.** Reuse and reflection are *not* two
mechanisms. An entity the user reuses is a **free variable occurring in both sides
of an application**, and GAL shares a free variable by a fan-in **on the middle
wire** (GAL p. 7, first picture — the application sharing graph); the two
occurrences are its aux ports. So reading `A`, `A->B`, `B->A` builds `(ab)a` with
`[a]==A`, `[b]==B` and `a` shared by such a fan — confirmed in code: compiling
`(ab)a` yields three fans where `(ab)` yields one, the extra being
`_Compiler._fan_in`'s `role=INTERNAL` node marked on `OFFSET`. The middle-wire
marking is what separates it from the syntactic λ/@ fans and makes an incoming
value *duplicate* (rule 4) rather than annihilate.

What **reflection** adds is not the fan but the **casts** (A left-up, B right-up),
plus the fact that its two parents are the reader's own new positions — which is
why that fan carries per-branch context. Positions stay independent either way:
contracting at one occurrence leaves the others alone.

*In the term representation* the one mechanism shows up two ways — a node reached
twice by identity, or an explicit `LamFan` when there are casts to record. That is
an artefact of writing graphs as terms, **not** a second kind of sharing: anything
testing for sharing must accept both (see `ontology_state._sharing_fan_above`).

Reflection is the only operation that shares A SUBJECT, the only one that increases `|Pr|`,
and the only one that breaks the typing. **No operation removes a pointer** — `Pr`
grows monotonically and is never empty.

## Verified expressiveness (§7)

Scope: applicative terms `t ::= x | (t t)`, no abstractions, plus sharing.
Exhaustive enumeration to 6 leaves; the sharing construction traced on a DAG.

- **Application shapes: COMPLETE**, and **reflection is what makes them so** —
  5/5, 14/14, 42/42 with **entity operands alone**. The obstruction is **pointer
  reachability**: contraction never *adds* a pointer, so alone it leaves the user
  on a single spine. `app(t,t)` duplicates the whole pointed subterm and yields a
  pointer on each copy, so both children can grow independently. **Closed-reading
  operands are therefore NOT what makes the calculus shape-complete** — useful for
  reusing finished work, but not load-bearing here.
- **Argument-position sharing: CONSTRUCTIBLE.** Reflect to stage the sharing, then
  contract with **option 2** at each aux-port pointer to re-parent the shared
  occurrence into argument position: `app(t,t)` → `app(app(a,t), app(b,t))` with
  `t` shared. Reflection is a **staging device**, and since it emits no
  scaffolding there is nothing to discharge afterwards. Full construction in
  `reading_desc` §7.2.
- **Still out of reach:** fan-out > 2 (a new reflection makes a new fan-in at
  its own level, not a third parent — see O3); cyclic sharing (always a DAG);
  late/arbitrary co-reference (§7.3(c), bears on O4).

## Working conventions

- **Verify against code, don't reason in your head.** `term_utils._term_type`
  for typing, `optimal_lambda/rules.py` for rule behaviour, exhaustive
  enumeration for expressiveness. Several conclusions here were corrected
  exactly that way — including the discovery that argument-position sharing *is*
  constructible, which an earlier draft had wrongly listed as impossible.
- **Marked provenance:** `[ASSUMPTION]` = adopted to make the definition total.
  Preserve these markers.
- The reading layer **never reduces** — it only builds terms. Nothing in the
  calculus calls `normalize` or any rule in `rules.py`.
- Vocabulary: reading (open/closed) · pointer · actPtr · contraction (option
  1/2) · reflection · type cast · occurrence · question/answer · staging.

## Open points to respect

Full list in `reading_desc` §10. The ones that will bite: **O1** restating the
typing rules so `[t]` takes an occurrence (mechanical, not yet done, and
`_term_type` is insufficient until it is); **O2** whether the reflection cast is
constrained at all — nothing relates A and B to C, so reflection may currently
retype a shared subterm to anything, which also qualifies §7.4's
KG-realisability claim; **O3** what the fan-in level means for a reading.

**The ontology layer** — a reading is the observation, an ontology is a model in
which it is valid — lives in `notes/reading_desc`
§8. It has its own skill: **`ontology`**. Load that rather than reasoning about it
from here. In brief: `ont(G(t), P)` with `P` corresponding elementwise to the
reading's pointers, contraction ↔ **R1**, reflection ↔ **R4**, maintained by
enrichment and refutation. Pointer stability under reduction — previously the hard
blocker — **is solved** (§8.5).
