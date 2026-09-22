"""Delegates — who invents the subqueries, the points and the options.

Two implementations of the engine's `StepSource`:

    MockSource   THE DEFAULT. No model call, no cost, no API key. Nonsense as
                 knowledge, well-formed as interactions: it reaches all three
                 cases, respects the 4-option cap, and runs out of points so a
                 query actually finishes. It is what makes `serve.py` runnable
                 on a fresh checkout.
    AgentSource  a real delegate over a `Transport` — `claude -p` or the
                 OpenAI API, reusing the transports unchanged.

WHY A MOCK IS WORTH REAL CARE. Every step of the loop is a paid call, so a
development pass over the UI would otherwise cost money each reload. The mock
lets the whole app be exercised — including the parts that only appear after
several steps — for free.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from interaction_engine import Entity, Option, Proposal

_STOP = {"the", "a", "an", "of", "in", "on", "to", "is", "are", "does", "do",
         "how", "what", "why", "when", "which", "and", "or", "for", "with",
         "it", "its", "that", "this", "by", "from", "as", "at", "be"}


def _slug(text: str) -> str:
    s = "".join(c if (c.isalnum() or c in " -") else " " for c in text.lower())
    return "-".join(s.split())[:48] or "thing"


class MockSource:
    """A delegate that invents plausibly-shaped material without a model."""

    def __init__(self, points_per_seed: int = 2) -> None:
        self.points_per_seed = points_per_seed
        self._seen: dict[str, int] = {}

    # ── decomposition ─────────────────────────────────────────────────────

    def seeds(self, query: str) -> list[tuple[str, Entity]]:
        """Content words become subqueries, in the order they appear."""
        words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z-]+", query)
                 if w.lower() not in _STOP and len(w) > 2]
        out: list[tuple[str, Entity]] = []
        for w in words[:4]:
            out.append((w, Entity(iri=_slug(w), label=w.lower(),
                                  gloss=f"what “{w}” means here")))
        if not out:
            out.append(("the question", Entity("the-question", "the question")))
        return out

    # ── proposals ─────────────────────────────────────────────────────────

    def propose(self, query: str, subquery: str, term, here: Entity,
                closed: list[str]) -> Proposal:
        """Cycle through the three cases, then declare the point settled."""
        n = self._seen.get(here.iri, 0)
        self._seen[here.iri] = n + 1
        if n >= self.points_per_seed:
            return Proposal(case=0)

        # deterministic, so a reload shows the same thing
        h = int(hashlib.sha1(f"{here.iri}:{n}".encode()).hexdigest(), 16)
        case = (1, 3, 2)[h % 3]
        base = here.short()

        if case == 1:
            c = Entity(f"{here.iri}-q{n}", f"what {base} refers to")
            return Proposal(
                case=1, point=subquery or base,
                question=f"What do you mean by “{base}”?",
                retype=c,
                options=[
                    Option(f"{base} as a substance",
                           entity=Entity(f"{here.iri}-subst", f"{base} (substance)"),
                           rationale="the thing itself"),
                    Option(f"{base} as a process",
                           entity=Entity(f"{here.iri}-proc", f"{base} (process)"),
                           rationale="what it does"),
                    Option(f"{base} as a measurement",
                           entity=Entity(f"{here.iri}-meas", f"{base} (measured)"),
                           rationale="how it is quantified"),
                ] + ([Option(f"what was read as {closed[-1]}",
                             closed_name=closed[-1],
                             rationale="an interaction you already finished")]
                     if closed else []),
            )

        if case == 2:
            return Proposal(
                case=2, point=subquery or base,
                question=f"What question is “{base}” the answer to?",
                retype=Entity(f"{here.iri}-asked", f"asking about {base}"),
                options=[
                    Option(f"about the cause of {base}",
                           entity=Entity(f"{here.iri}-cause", f"cause of {base}")),
                    Option(f"about the effect of {base}",
                           entity=Entity(f"{here.iri}-effect", f"effect of {base}")),
                ],
            )

        return Proposal(
            case=3, point=subquery or base,
            question=f"Is “{base}” better seen as a question and an answer?",
            options=[
                Option("cause held against effect",
                       entity_a=Entity(f"{here.iri}-cause", f"cause of {base}"),
                       entity_b=Entity(f"{here.iri}-effect", f"effect of {base}"),
                       rationale="two sides of the same point"),
                Option("general held against particular",
                       entity_a=Entity(f"{here.iri}-gen", f"{base} in general"),
                       entity_b=Entity(f"{here.iri}-part", f"this {base}")),
            ],
        )


class AgentSource:
    """A real delegate over a `Transport`.

    Kept deliberately small: the transports and their schemas already exist, so
    this only says WHAT to ask and how to read the answer back as engine types.
    """

    _SCHEMA = {
        "type": "object",
        "properties": {
            "case": {"type": "integer", "description": "1, 2 or 3; 0 if clear"},
            "point": {"type": "string"},
            "question": {"type": "string"},
            "retype": {"type": "string",
                       "description": "what the point is asking about"},
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "rationale": {"type": "string"},
                        "entity": {"type": "string"},
                        "entity_a": {"type": "string"},
                        "entity_b": {"type": "string"},
                    },
                    "required": ["label"],
                },
            },
        },
        "required": ["case"],
    }

    _SEEDS = {
        "type": "object",
        "properties": {
            "seeds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "quote": {"type": "string",
                                  "description": "words from the query, verbatim"},
                        "entity": {"type": "string"},
                    },
                    "required": ["quote", "entity"],
                },
            },
        },
        "required": ["seeds"],
    }

    def __init__(self, transport) -> None:
        self.transport = transport

    @staticmethod
    def _ent(name: str) -> Optional[Entity]:
        name = (name or "").strip()
        if not name:
            return None
        return Entity(iri=_slug(name), label=name)

    def seeds(self, query: str) -> list[tuple[str, Entity]]:
        prompt = (
            "Break this question into the points that would have to be clear "
            "before it can be answered. Quote the words of the question "
            "verbatim, and name the thing each one is about.\n\n"
            f"QUESTION: {query}\n\nGive at most 4.")
        data = self.transport.invoke(prompt, self._SEEDS)
        out = []
        for s in (data.get("seeds") or [])[:4]:
            ent = self._ent(s.get("entity", ""))
            if ent is not None:
                out.append((s.get("quote", ""), ent))
        return out

    def propose(self, query: str, subquery: str, term, here: Entity,
                closed: list[str]) -> Proposal:
        prompt = (
            "A user is clarifying their question one point at a time.\n\n"
            f"QUESTION: {query}\n"
            f"THE PART BEING READ: {subquery}\n"
            f"WHERE THEY STAND: {here.short()}\n\n"
            "If something here is still unclear, choose ONE shape:\n"
            "  case 1 — the point needs saying what it refers to; give the "
            "question and 2-4 answers to choose between.\n"
            "  case 2 — what they have is an answer; give 2-4 questions it "
            "could be answering.\n"
            "  case 3 — the point is really a question and an answer held "
            "together; give 2-4 such pairs.\n"
            "If nothing is unclear, return case 0.")
        data = self.transport.invoke(prompt, self._SCHEMA)
        case = int(data.get("case") or 0)
        if case not in (1, 2, 3):
            return Proposal(case=0)
        opts = []
        for o in (data.get("options") or []):
            opts.append(Option(
                label=o.get("label", ""), rationale=o.get("rationale", ""),
                entity=self._ent(o.get("entity", "")),
                entity_a=self._ent(o.get("entity_a", "")),
                entity_b=self._ent(o.get("entity_b", "")),
            ))
        return Proposal(case=case, point=data.get("point", ""),
                        question=data.get("question", ""),
                        retype=self._ent(data.get("retype", "")),
                        options=opts).trimmed()
