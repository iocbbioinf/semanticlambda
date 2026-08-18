---
name: reading-interpretation
description: The INTERPRETATION of the Reading calculus — what contraction, reflection, abstraction, questions and answers MEAN as movements of sense in a subjective user. Covers contraction as sense-becoming-and-ceasing, the negative/positive sides of abstraction (title vs lambda), why readings are unshareable and subjective, why ontologies approximate a hidden intention, and why GAL sharing graphs are used (locality without understanding another user's question). Use when explaining WHY the reading calculus has the shape it does, when a design decision needs justifying from the interpretation, or before changing the operations. For the formal spec, use the sibling `reading` skill.
---

# The Reading Calculus — Interpretation

This skill records **what the reading calculus means**, as given by Marek across
2026-08-13/14. The sibling skill `reading` and `notes/reading_desc` hold the
**formalism**; this holds the account the formalism is a record of. When the two
appear to conflict, the interpretation is the intent and the formalism is the
approximation — but **do not silently change the formalism to match**; raise it.

Everything here is Marek's account, restated. It is not settled mathematics and
not my inference. Where I have marked something as a consequence, it is a
consequence *he drew*, not one I added.

## The premise: reading is subjective and unshareable

**Readings are not shareable among users, because one user does not see into the
head of another. A reading is totally subjective.**

This is the basic precondition of the whole design, not a preference. Everything
below follows from it — most sharply, why a user can never see the positive form
of another user's abstraction (below), and why ontologies can only ever
*approximate* what a contracting user meant.

## Contraction is the movement of sense

The formal step records a process; the process is the point.

**A** is the place the user has arrived at. By **viewing (reflecting)** it, A
becomes a **question**: the user's sense of A breaks — is lost — and in that same
movement **B emerges as the new sense of the gap in A**.

- **B is first totally outside A.** One may say A becomes outside as such in the
  form of B, or better: **A is becoming B**. This becoming **is** the
  contraction, and it is a **subjective activity of the user**.
- But the same reflection that constitutes the becoming **internalises B**. That
  is why contraction **ceases**: sense eludes. Since **contraction is sense** —
  by contracting, the user views sense — viewing it consumes it. We say the user
  **contracts B**.
- When the becoming ceases, **the ceasing itself becomes the next question**, and
  C emerges as its answer: `t = (ab)c`. Contraction continues.

So contraction is not a static relating of two places. It is a movement that
**exhausts itself and thereby generates its own successor**.

**Consequences Marek draws from this** (they explain the formalism's shape):

- The type of a contraction `t` is the **rightmost leaf** of `G(t)`, and actPtr
  sits at the **root**. Contraction is **appropriate sense flow in a binary
  tree** — the rightmost leaf is where the flow has reached.
- A user's **already-done contractions can themselves be answers**. So
  `t = (t1 t2)` may contract two contractions, e.g. `(ab)(cd)`. **This** is why
  every applicative term — every binary tree — is constructible.

## Reflection is contraction reflected from within

The user **stays at C** and reflects `A -> B`. It reflects the contraction
`A -> B` **from within** the user's own contraction: **neither A, nor B, nor
A->B is outside** the user's contraction ending at C.

That is the sharp contrast with contraction, where **B is outside**.

Reflection **forks into two scopes** (spaces), expressed by A and B. Both
contractions may continue, but **in different scopes** — the reflection is
reflected as *becoming the scope entity*. Hence `G(app(t, t))` with `t` shared by
a fan-in.

**A and B are scopes, not material.** They do not appear in the term at all: the
step emits **no fresh variables**, and A and B are **cast onto the two
occurrences** of the shared `t` — left-up typed A, right-up typed B. The forked
becoming *is* the scope, so it types an occurrence rather than adding a thing.

That is why reflection **breaks the typing**: one shared `t` reads as A through one
occurrence and B through the other, while its own type was C. Nothing became
outside, so nothing new was built — only the same subject, viewed under two
aspects. (Formal consequences in `reading_desc` §4.2; `[t]` stops being a function
of the term.)

Readings I got wrong here, corrected by Marek — do not re-propose: A and B are
**not** "contexts" in the scope/discharge sense (that also collides with GAL's own
*context semantics*), **not** "concepts to be developed", and there are **no
scaffolding variables** to discharge.

A contraction **may** explicitly reach A (resp. B) by reading development. It is
fine for a reading to be closed with **some forked contractions left
undeveloped**.

## Abstraction has two sides

Abstraction arises when the user contracts a place B and **no answer emerges**.
Continuing to contract, the contraction comes to contract *itself*: contraction
becomes its own outside, the user views self as something outside — but sense is
breaking. So the answer is **the reflection of that breaking**: the user
expresses the question **explicitly**. A question is the expressing of losing
sense, of something missing or broken — **stated in negative form**.

The result of this self-reflection: the user designates an **arbitrary entity**
pointing at the place A that **missed sense** (an empty hole, a nonsense) **in
the context of the contraction**.

| side | what it expresses | form | visible to a reading? |
|---|---|---|---|
| **abstraction title** | the *breaking* — losing the sense of a contraction | **negative** | **yes** — contracted just like an entity |
| **lambda abstraction** `G(lam a.t)` | the *form* of the breaking (its context) | **positive** | **no** |

**Abstraction is the positive form of the question** — it gives form (context) to
the negative, the questioned entity. It is the **explicit form of how becoming**
works.

### Three things that follow, and are easy to get wrong

1. **Creating an abstraction is NOT part of reading.** It happens **after the
   reading is done (closed)**. So a reading still contains **no abstractions** —
   the formal invariant survives. Abstraction is not a fourth reading step.
2. **A reading sees only titles.** Because readings are subjective and
   unshareable, the user never sees the *positive* form. They select a **title** —
   of an entity, or of a **concrete abstraction of the same type** — and contract
   it exactly as an entity is contracted.
3. **Abstracting retypes.** `[G(lam a.t)] == [a]`, which **differs** from `[t]`,
   the type of the reading it was made from. This is why "all abstractions of
   type A" is a real, finite query.

### Abstraction as a little ontology

An abstraction of entity A is interpretable as **a little ontology around A that
some user created**. It becomes accessible to *other* users **by title** during
their reading — a user may select not just an entity (by title) but a **concrete
abstraction** (by title) of the same type.

This is the only channel by which subjective, unshareable readings accumulate
shared material: **titles**.

## The aim: ontologies created simultaneously with reading

A reading's contraction `A -> B` is **implicit, subjective, hidden in the mind of
the user**. The aim is to express it **explicitly** by **instances** — the
positive parts of abstractions — of A and B.

Then **reduction** of that is the **explicit act of answering the question**: an
explicit contraction, *in the calculus* rather than in the user's mind, which
**may (not necessarily) approximate the intention** of the contracting user — the
ontology of `A -> B`.

**If contraction continues and this ontology still fits, the approximation gets
better and better.** Only instances that fit the reduction rule survive as
ontologies for this reading; survivors are better approximations.

**The formal definition now exists** (Marek, 2026-08-14): `notes/reading_desc` §8,
with its own skill **`ontology`**. This section remains the *intent* the
definition formalises — read it first, then §8. Do not invent beyond either.

## Why GAL sharing graphs

**So that every reading step (reduction step in the graph) is LOCAL** — made by
the user from one concrete place, seeing only the neighbours entering the local
reduction rule (asking/answering questions), **without the necessity of
understanding the whole question of another user.**

That last clause is the real motivation, and it is stronger than "the graph is
unknown": locality is what makes subjective, unshareable readings composable at
all.

### The apparent locality violation in reflection — and its answer

It *seems* reflection breaks locality, since A and B need not be neighbours of C.
The answer has two parts:

1. Reflection is **inside** contraction; and **reading traverses a graph that
   does not fully exist — the graph is created by traversing it, retroactively.**
2. As with contraction, **only instances that fit GAL rule R4 remain** as
   ontologies for the reading. Again, these are better approximations.

So locality is preserved not by constraining what the user may reach, but by
**what survives the local rule**.

## How to use this skill

- **Justifying a design decision:** look here first. Most "why is it this way"
  questions about the operations are answered by the sense-movement account or by
  the subjectivity premise.
- **Before changing an operation:** check the interpretation still holds. The
  formalism is an approximation of this account, so a change that is formally
  clean may be interpretively wrong.
- **Do not import terminology loosely.** "Context" and "scope" are used in
  specific senses here, and *context semantics* is an unrelated GAL term used
  elsewhere in this project.
- **Marked provenance:** everything above is Marek's account. `[CLARIFIED]` items
  in `notes/reading_desc` cross-reference it. Two of my own readings were
  corrected in the conversation that produced this skill — recorded above so they
  are not re-proposed.
