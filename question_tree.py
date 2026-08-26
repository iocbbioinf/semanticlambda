"""Questions as a SUBTYPE HIERARCHY — notes/reading_desc §2 and §8.2.

A QUESTION NAMES A SUBTYPE. Where §2 said "types are exactly entities of the
knowledge graph", a question `lam a.t` with [a] == A defines a NEW type A1 < A —
the type of "A, read through this question". So the types of the calculus are the
entities AND the subtypes questions name.

    q1 = (lam a.((b a) c))       [a]  == A     [q1] == A1,  A1 < A
    q2 = (lam q1.(e (q1 d)))     [q1] == A1    [q2] == A2,  A2 < A1

    A            the entity — the MOST GENERAL question of its type, the root
    +- A1        q1
       +- A2     q2

THE PARENT LINK IS A CONSEQUENCE, NOT A DECLARATION. Creating q2 means: build its
body by READING, contracting q1 in BY ITS TITLE (§4.1 admits a title as an
option-1 operand), then bind that occurrence. The binder's type is then q1's type,
so q1 IS the parent. Nothing composes the nesting by hand; it is what the reading
used.

I6 SURVIVES. A reading contains no abstractions. Selecting a question puts a
VARIABLE in the reading whose type is the subtype that question names; the
reading renders the question's TITLE. §8.2's split does the work: a reading sees
titles, the ontology layer sees the positive form G(lam a.t).

WHY THIS EARNS ITS KEEP: it CONSTRAINS the ontology set. Enrichment at a variable
of type A1 may use only questions of A1 or below — not every question of A. A more
specific question means fewer surviving models, which is the first direct control
a reader has over refutation.

IDENTITY IS A MINTED IRI, not the term string. `term_str` cannot be the key for
two reasons:

  * the same term under DIFFERENT parents names two different subtypes;
  * the same term under the SAME parent with a DIFFERENT TITLE is a DIFFERENT
    QUESTION — the title is part of what is asked, not a label on top of it — so
    it names its own subtype too.

Each question record therefore carries:

    qid     the minted iri naming ITS OWN subtype
    parent  the iri of the type its binder has — a question's qid, or the ENTITY
            iri at the root of the tree
"""

from __future__ import annotations

import hashlib
from typing import Optional

from kg_store import load_lambda_db, save_lambda_db, local_name

# Minted subtype iris live in their own namespace so they can never collide with
# a knowledge-graph entity, and so `is_question_type` is a syntactic test.
QUESTION_NS = "https://ahoj-db.org/question#"


def is_question_type(iri: str) -> bool:
    """Is this iri a subtype some question names, rather than a KG entity?"""
    return bool(iri) and iri.startswith(QUESTION_NS)


def mint_qid(term_str: str, parent_iri: str, title: str = "") -> str:
    """The iri for the subtype a question names.

    Derived from (parent, term, TITLE), which makes it STABLE — re-deriving the
    tree from the same records yields the same iris, so a saved reading that
    refers to a subtype stays valid — while separating every case that is a
    distinct question:

      same term, different parents  -> different subtypes (different reading)
      same term, same parent,
        different titles            -> DIFFERENT QUESTIONS, so different
                                       subtypes: the title is part of what is
                                       asked, not a label on top of it.

    A uuid would separate them too but would not be stable, so ids could not be
    re-derived and a saved reading could dangle.
    """
    key = f"{parent_iri} | {term_str} | {title}"
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    return f"{QUESTION_NS}{h}"


# ── the tree ────────────────────────────────────────────────────────────────

class QuestionTree:
    """The subtype hierarchy over the saved questions.

    Built from the question store: each record's `parent` is the type its binder
    has, so the records ARE the edges. A record with no parent recorded is a
    direct child of its bound entity — which is what every question saved before
    the hierarchy existed is, since nothing then could nest one question in
    another's binder.
    """

    def __init__(self, records: Optional[list[dict]] = None) -> None:
        self._by_qid: dict[str, dict] = {}
        self._children: dict[str, list[str]] = {}
        self._parent: dict[str, str] = {}
        self.load(records)

    # -- construction ------------------------------------------------------
    def load(self, records: Optional[list[dict]] = None) -> None:
        recs = load_lambda_db() if records is None else records
        self._by_qid.clear(); self._children.clear(); self._parent.clear()
        for rec in recs:
            d = rec.get("term")
            if not isinstance(d, dict) or d.get("type") != "abs":
                continue
            entity = (d.get("var") or {}).get("iri")
            if not entity:
                continue
            # a record written before the hierarchy has no parent: its binder
            # types as the ENTITY, so the entity is its parent.
            parent = rec.get("parent") or entity
            qid = rec.get("qid") or mint_qid(
                rec.get("term_str", ""), parent, rec.get("chain_label", ""))
            self._by_qid[qid] = rec
            self._parent[qid] = parent
            self._children.setdefault(parent, []).append(qid)

    # -- queries -----------------------------------------------------------
    def record(self, qid: str) -> Optional[dict]:
        return self._by_qid.get(qid)

    def parent_of(self, iri: str) -> Optional[str]:
        """The type this one is a subtype OF, or None at a root (an entity)."""
        return self._parent.get(iri)

    def children_of(self, iri: str) -> list[str]:
        """The qids of the questions immediately below `iri`.

        `iri` may be an ENTITY (the tree's root, the most general question of its
        type) or another question's qid.
        """
        return list(self._children.get(iri, ()))

    def ancestors_of(self, iri: str) -> list[str]:
        """From `iri`'s parent up to the entity at the root, nearest first."""
        out: list[str] = []
        seen = {iri}
        cur = self._parent.get(iri)
        while cur is not None and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self._parent.get(cur)
        return out

    def entity_of(self, iri: str) -> str:
        """The KG entity at the root of `iri`'s tree.

        An entity is its own root. Every question chain ends at one, since a
        record's parent defaults to its bound entity.
        """
        if not is_question_type(iri):
            return iri
        anc = self.ancestors_of(iri)
        for a in reversed(anc):
            if not is_question_type(a):
                return a
        return iri

    def is_subtype(self, sub: str, sup: str) -> bool:
        """Is `sub` the same type as `sup`, or below it?

        REFLEXIVE: a type is a subtype of itself, so "questions of type A1 or
        below" includes A1's own. That is what enrichment needs — the question
        naming A1 is itself usable at a variable of type A1.
        """
        if sub == sup:
            return True
        return sup in self.ancestors_of(sub)

    def subtypes_of(self, iri: str) -> list[str]:
        """`iri` and every type below it, depth-first."""
        out = [iri]
        stack = list(self._children.get(iri, ()))
        while stack:
            q = stack.pop()
            out.append(q)
            stack.extend(self._children.get(q, ()))
        return out

    def title_of(self, iri: str) -> Optional[str]:
        """The question title for a subtype iri, or None for an entity."""
        rec = self._by_qid.get(iri)
        return rec.get("chain_label") if rec else None

    def depth_of(self, iri: str) -> int:
        """How many questions deep — 0 for an entity, 1 for a question on it."""
        return len(self.ancestors_of(iri))

    def all_qids(self) -> list[str]:
        return list(self._by_qid)

    def roots(self) -> list[str]:
        """The entities that have at least one question."""
        return [i for i in self._children if not is_question_type(i)]


# ── the shared instance ─────────────────────────────────────────────────────

_TREE: Optional[QuestionTree] = None


def tree(refresh: bool = False) -> QuestionTree:
    """The question tree, built once and reused.

    Pass refresh=True after saving a question, as `abstraction_titles` is
    refreshed for the same reason.
    """
    global _TREE
    if _TREE is None or refresh:
        _TREE = QuestionTree()
    return _TREE


# ── writing the identity onto the records ───────────────────────────────────

def assign_ids(records: Optional[list[dict]] = None,
               write: bool = False) -> tuple[list[dict], int]:
    """Give every question record a `qid` and a `parent`, in place.

    Idempotent: a record that already has both is left alone, and the minted iri
    is derived from (term, parent), so re-running yields the same ids.

    Returns (records, how many were given an id).
    """
    recs = load_lambda_db() if records is None else records
    n = 0
    for rec in recs:
        d = rec.get("term")
        if not isinstance(d, dict) or d.get("type") != "abs":
            continue
        entity = (d.get("var") or {}).get("iri")
        if not entity:
            continue
        if not rec.get("parent"):
            # nothing could nest a question in another's binder before the
            # hierarchy existed, so every existing question is a direct child
            # of its bound entity.
            rec["parent"] = entity
            n += 1
        if not rec.get("qid"):
            rec["qid"] = mint_qid(rec.get("term_str", ""), rec["parent"],
                                  rec.get("chain_label", ""))
            n += 1
    if write:
        save_lambda_db(recs)
        tree(refresh=True)
    return (recs, n)
