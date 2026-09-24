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

        # ONE pair: reflection sees this point from its question side and its
        # answer side, over one shared node. There is no rival pair to offer.
        return Proposal(
            case=3, point=subquery or base,
            question=f"Is “{base}” better seen as a question and an answer?",
            options=[
                Option("cause held against effect",
                       entity_a=Entity(f"{here.iri}-cause", f"cause of {base}"),
                       entity_b=Entity(f"{here.iri}-effect", f"effect of {base}"),
                       rationale="two sides of the same point"),
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
            "case": {"type": "integer",
                     "description": "1 the words are vague; 2 they are definite "
                                    "but what they are for is not; 3 they hold "
                                    "a question and its answer together; "
                                    "0 nothing is unclear here"},
            "point": {"type": "string",
                      "description": "the words being read, verbatim"},
            "question": {"type": "string",
                         "description": "the question put to the user"},
            "retype": {"type": "string",
                       "description": "what the point is asking about"},
            "options": {
                "type": "array",
                "description": "2-4 rival readings for case 1 and 2; EXACTLY "
                               "ONE for case 3, whose single pair is the "
                               "point's question side and answer side, not a "
                               "choice between splits",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string",
                                  "description": "case 1: a meaning of the "
                                                 "words; case 2: a question "
                                                 "they could answer; case 3: "
                                                 "what the pair is"},
                        "rationale": {"type": "string",
                                      "description": "one clause: why this "
                                                     "reading is available"},
                        "entity": {"type": "string",
                                   "description": "CASES 1 AND 2 ONLY, and "
                                                  "REQUIRED there: what this "
                                                  "option is about. Leave out "
                                                  "for case 3"},
                        "entity_a": {"type": "string",
                                     "description": "CASE 3 ONLY, required: "
                                                    "the QUESTION side"},
                        "entity_b": {"type": "string",
                                     "description": "CASE 3 ONLY, required: "
                                                    "the ANSWER side"},
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
        # THE THREE CASES MUST BE TOLD APART BY SOMETHING VISIBLE IN THE QUERY.
        # Described from the calculus ("what they have is an answer") they mean
        # nothing to a delegate that does not know the calculus, so it fills in
        # the first case it understands and every step comes back as case 1.
        # Each is therefore given a TEST on the words being read, a worked
        # example, and the fields it must fill — and the order is not a ranking.
        prompt = (
            "Don't use context.\n"
            "A user is clarifying their question one point at a time. Do NOT "
            "answer the question; find what is unclear in the part being "
            "read.\n\n"
            f"QUESTION: {query}\n"
            f"THE PART BEING READ: {subquery}\n"
            f"WHERE THEY STAND: {here.short()}\n\n"
            "Choose the ONE case that fits best (most clarifies)"
            "  case 1 — THE WORDS ARE VAGUE. They could name several different "
            "things, and which one is meant changes the answer.\n"
            "    FILL: `question` asking which is meant; 2-4 `options`, each "
            "with a `label` and the `entity` it means.\n\n"
            "  case 2 — THE WORDS ARE ALREADY DEFINITE, but why they were asked "
            "for is not. The user named something specific; what is unclear is "
            "the larger question it serves, because that decides what a good "
            "answer includes.\n"
            "what they are FOR?\n"
            "    e.g. “the human 5-HT2C receptor” is not vague — but is it "
            "asked as a drug target, as a selectivity counter-screen, or as a "
            "sequence?\n"
            "    FILL: 2-4 `options`, each `label` a question these words could "
            "be answering, with the `entity` that question is about.\n\n"
            "  case 3 — THE WORDS COULD BE CLARIFIED by giving question/answer pair\n"
            "    FILL: EXACTLY ONE `option`: `entity_a` the question side, "
            "`entity_b` the answer side. No rival pairs.\n\n"
            "If nothing here is unclear, return case 0 and no options.")
        data = self.transport.invoke(prompt, self._SCHEMA)
        case = int(data.get("case") or 0)
        if case not in (1, 2, 3):
            return Proposal(case=0)
        # AN OPTION THE CASE CANNOT USE IS DROPPED HERE, at the boundary. The
        # schema requires only `label`, and `_ent` returns None for a missing or
        # blank entity string, so a delegate that names an option without saying
        # what it maps to would otherwise reach `_operand` as `entity=None` and
        # crash the step the user just clicked. Each case has its own
        # requirement (see `Option`): 1 and 2 need `entity`, 3 needs BOTH sides.
        opts = []
        for o in (data.get("options") or []):
            label = (o.get("label") or "").strip()
            if not label:
                continue
            ent = self._ent(o.get("entity", ""))
            ent_a = self._ent(o.get("entity_a", ""))
            ent_b = self._ent(o.get("entity_b", ""))
            # THE FIELD, NOT THE MEANING, IS WHAT GOES WRONG. A delegate that
            # picked the right case and then put the entity in the neighbouring
            # field loses every option below and the step degrades to case 0 —
            # which reads as "it never picks case 2 or 3". Recover only where
            # the intent is unambiguous: one side named under the other case's
            # field. Never invent a side that was not sent.
            if case in (1, 2) and ent is None and ent_b is None:
                ent, ent_a = ent_a, None
            elif case == 3 and ent is not None and (ent_a is None) != (ent_b is None):
                if ent_a is None:
                    ent_a, ent = ent, None
                else:
                    ent_b, ent = ent, None
            opt = Option(
                label=label, rationale=o.get("rationale", ""),
                entity=ent, entity_a=ent_a, entity_b=ent_b,
            )
            if case == 3:
                if opt.entity_a is None or opt.entity_b is None:
                    continue
            elif opt.entity is None and opt.closed_name is None:
                continue
            opts.append(opt)
        return Proposal(case=case, point=data.get("point", ""),
                        question=data.get("question", ""),
                        retype=self._ent(data.get("retype", "")),
                        options=opts).trimmed()
