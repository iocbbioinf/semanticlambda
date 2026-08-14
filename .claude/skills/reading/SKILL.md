---
name: reading
description: The Reading calculus — this project's model of a user reading a knowledge graph as a typed applicative lambda term built bottom-up in a sharing graph. Covers the one contraction step (two options), the reflection step, closing a pointer, typing-as-location, and verified expressiveness results including how argument-position sharing is constructed. Use when reading, explaining, extending, implementing or debugging anything about readings, pointers, contraction, reflection, questions, open vs closed readings, or sharing.
---

# The Reading Calculus

This project models **a user reading a knowledge graph** as the incremental,
bottom-up construction of a typed applicative lambda term held as a sharing
graph.

**Authoritative specification, do not duplicate — read this file:**

- `notes/reading_desc` — the prose spec (§1–§10): what a reading is, typing, the
  steps, invariants, expressiveness, **§8 ontology**, worked examples, open
  points.

It was rewritten from scratch 2026-08-13 against Marek's specification of that
date. **Earlier drafts are superseded and their history is not preserved** — if
you recall cases numbered 1/2/4/5, pointer adoption from grafted readings, or an
ontology layer built on those cases, that material is gone. Do not reintroduce
it.

There is deliberately **no pseudocode companion**. A `notes/reading_alg` existed
and was **deleted 2026-08-13** while the calculus is still settling — it is
recoverable from git (`git show 4c879ac:notes/reading_alg`) but describes the
superseded concept. Do not recreate it unless asked.

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
pointers to **edges**. **CLOSED** once saved: the graph **alone**, no pointers.

Before each step the user selects one pointer, **actPtr** — *the place where the
user stays*. Pointers exist only because of reflection; contraction never
changes their number.

**The two states are not two kinds of object.** A closed reading is *de facto
equivalent to a reading with one pointer, to the root of its graph* — closing
**collapses** the pointer set to the root. Hence: saving is **total** (closable
from any state, empty `Pr` included); re-opening is well defined for every closed
reading; and **no pointer set need be stored**, so the existing `(name, term)`
persistence is complete by construction. *Scope:* this says what a closed reading
**is**, not that the two are interchangeable everywhere — §4's steps act on an
open reading, so a closed one must be re-opened before being stepped on.

**Opening** (§3), two forms: a **type** A → `(G(a), {ptr to a})`, or an **already
saved reading** → `(G(t), {ptr to G(t)})`. Both start with exactly one pointer.

**`Pr` may be empty** — closing the last pointer is legal (O3 resolved). No step
can then fire, but the reading is still savable: a **terminal** state, not a stuck
one. Do not assert non-emptiness.

A closed reading contributes only its **type** and graph when used later. This
is why there is a single contraction case for entities and closed readings
alike: **there is never a pointer set to adopt or discard.**

## The steps

**Contraction** — one case, two options. Relates **two places**, both prepared
beforehand: the selected operand (entity or closed reading, type B) and where
the user stays (actPtr, type A). Not a movement — after it **the user stays at
both places at once**.

| | builds | type after | reading |
|---|---|---|---|
| **option 1** | `app(t1, t2)` | **B** | A asks, B answers |
| **option 2** | `app(t2, t1)` | **A** (unchanged) | B asks, A answers |

Option 2's unchanged type is not a failure to move: A is one of the two occupied
places, and it is the one actPtr designates.

**Reflection** — user stays at C, selects `A -> B`, and reflects A as question
against B as answer without moving to either:

```
G(t1)  |->  app( app(t1,a), app(t1,b) )     with t1 SHARED by a fan-in
```

`actPtr` becomes two pointers on the fan-in's **left-up/right-up aux ports** —
`grey`/`black` in `_Compiler._fan_in`. **They designate the aux-port edges, i.e.
the two occurrences of `t1`, not the enclosing app nodes.** This is the single
most consequential detail in the spec (see sharing, below). Reflection is the
only operation that shares and the only one that increases `|Pr|`.

**Closing a pointer** — reflection's inverse. Close `p` iff it points to `t1`,
`app(t1,a)` is a subgraph, `[t1]==[a]`, and `a` was added **by a reflection
step**. Result replaces `app(t1,a)` by `t1`. The provenance condition is not
visible in the graph and must be **recorded when reflection creates the
variables** (O2).

## Verified expressiveness (§7)

Scope: applicative terms `t ::= x | (t t)`, no abstractions, plus sharing.
Exhaustive enumeration to 6 leaves; the sharing construction traced on a DAG.

- **Application shapes: COMPLETE** — but only because the operand may be a
  **closed reading**. With entities only it fails: 4/5 at 4 leaves, 8/14 at 5,
  16/42 at 6 (reflection helps — 5/5, 12/14, 28/42 — but doesn't close it). The
  obstruction is **pointer reachability**: grafting an entity never *adds* a
  pointer, so the user extends along a single spine. **Closing a reading is
  therefore load-bearing, not a convenience.**
- **Argument-position sharing: CONSTRUCTIBLE.** Reflect `C -> C` to stage
  sharing, contract with **option 2** at each aux-port pointer to re-parent the
  shared occurrence into argument position, then close both pointers to discard
  the scaffolding. Yields `app(app(a,t), app(b,t))` with `t` shared. Reflection
  is used as a **staging device**. Full construction in `reading_desc` §7.2.
- **Still out of reach:** fan-out > 2 (a new reflection makes a new fan-in at
  its own level, not a third parent — see O4); cyclic sharing (always a DAG);
  late/arbitrary co-reference (§7.3(c), bears on O5).

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
  1/2) · reflection · closing a pointer · question/answer · staging.

## Open points to respect

Full list in `reading_desc` §9. **O1 and O3 are resolved** (re-opening mints one
pointer at the root; closing the last pointer is legal). The ones that will bite:
**O2** recording reflection provenance, needed before closing can be implemented
at all; **O4** what the fan-in level means and whether closing one branch
collapses the fan.

**The ontology layer** — a reading is the observation, an ontology is a model in
which it is valid — was **defined 2026-08-14** and lives in `notes/reading_desc`
§8. It has its own skill: **`ontology`**. Load that rather than reasoning about it
from here. In brief: `ont(G(t), P)` with `P` corresponding elementwise to the
reading's pointers, contraction ↔ **R1**, reflection ↔ **R4**, maintained by
enrichment and refutation. Pointer stability under reduction — previously the hard
blocker — **is solved** (§8.5).
