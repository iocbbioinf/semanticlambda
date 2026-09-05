"""A mock delegate — the app without Claude Code.

Same interface as `reading_agent.ReadingAgent` (`seed_entities`, `propose_step`,
`total_cost_usd`), so the driver cannot tell the difference. This is the DEFAULT
backend: ordinary runs cost nothing and need no CLI. Pass `--claude` to delegate
for real.

WHAT IT IS FOR. Exercising the calculus, not producing meaning. The entities are
derived from the query's own words and a small stock vocabulary, so the readings
are nonsense as knowledge — but they are well formed as readings, and they hit
all three interaction kinds, which is what the app's own machinery needs tested:

    A  contraction option 1 — the reader moves
    B  reflection           — the reading splits, |Pr| grows
    C  contraction option 2 — the reader stays

It is DETERMINISTIC given a query (seeded from the query text), so a run can be
repeated exactly — with `--seed` to vary it deliberately.

The step kind is chosen by a fixed cycle rather than at random, so every kind is
reached in a short session instead of A dominating as the real delegate tends to.
"""

from __future__ import annotations

import random
import re
from typing import Optional

from reading_agent import Entity, Option, StepProposal

# Stock material to build plausible-looking options from. Deliberately generic:
# the mock makes no claim to know anything about the query's subject.
_FACETS = [
    ("as a process", "taken as something that unfolds over time"),
    ("as a structure", "taken as an arrangement that holds still"),
    ("as an agent", "taken as something that acts on others"),
    ("as an effect", "taken as what results rather than what causes"),
    ("as a part", "taken as a component of something larger"),
    ("as a whole", "taken as the thing its parts compose"),
]

_RELATIONS = [
    ("what it does", "what it is for"),
    ("what precedes it", "what follows from it"),
    ("its mechanism", "its outcome"),
    ("the general case", "this particular case"),
]

_PREDECESSORS = [
    ("the wider context", "the setting this was approached from"),
    ("the prior question", "what was being asked before this"),
    ("the neighbouring case", "a parallel this was read against"),
]

_STOP = {
    "how", "does", "do", "did", "what", "is", "are", "was", "were", "why",
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "by",
    "with", "that", "this", "it", "its", "as", "at", "from", "be", "can",
    "which", "when", "where", "who", "whom", "there", "their", "has", "have",
}


def _slug(text: str) -> str:
    s = "".join(c if (c.isalnum() or c in " -") else " " for c in text.lower())
    return "-".join(s.split())[:48].strip("-") or "thing"


class MockAgent:
    """Stands in for `claude -p`. Same interface, no subprocess, no cost."""

    def __init__(self, seed: Optional[int] = None, model: str = "mock") -> None:
        self.model = model
        self._seed = seed
        self.total_cost_usd = 0.0
        self.last_cost_usd = 0.0
        self._rng = random.Random(seed)
        self._steps = 0

    # ── the two calls the driver makes ────────────────────────────────────

    def seed_entities(self, query: str, limit: int = 6) -> list[Entity]:
        """Entities from the query's own content words, plus stock fillers."""
        if self._seed is None:
            # Deterministic per query, so a run can be repeated exactly.
            self._rng = random.Random(_slug(query))

        words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9-]+", query)
                 if w.lower() not in _STOP and len(w) > 2]
        seen, ents = set(), []
        for w in words:
            slug = _slug(w)
            if slug in seen:
                continue
            seen.add(slug)
            ents.append(Entity(iri=f"local:{slug}", label=w,
                               gloss="taken from the query"))
        for extra in ("the question itself", "the surrounding field",
                      "the underlying case"):
            if len(ents) >= max(3, limit):
                break
            slug = _slug(extra)
            if slug not in seen:
                seen.add(slug)
                ents.append(Entity(iri=f"local:{slug}", label=extra,
                                   gloss="a stock entity of the mock"))
        return ents[:limit]

    def propose_step(self, query: str, term_text: str, here: Entity,
                     history: list[str], reached_from: Optional[str],
                     known: list[Entity], allow_c: bool) -> StepProposal:
        """Cycle A → B → C so every kind is exercised in a short session.

        Ends the reading after a few steps by answering "none", which is what
        drives the app on to the next entity — the branch that would otherwise
        need a long real session to reach.
        """
        self._steps += 1
        if self._steps > 6:
            self._steps = 0
            return StepProposal(kind="none",
                                note="the mock has said all it has to say here")

        order = ["A", "B", "C"] if allow_c else ["A", "B"]
        kind = order[(self._steps - 1) % len(order)]

        if kind == "A":
            return self._kind_a(here, known)
        if kind == "B":
            return self._kind_b(here)
        return self._kind_c(here)

    # ── the three kinds ───────────────────────────────────────────────────

    def _kind_a(self, here: Entity, known: list[Entity]) -> StepProposal:
        """An entity understood several ways -> contraction option 1."""
        facets = self._rng.sample(_FACETS, 3)
        options = [
            Option(kind="A", label=f"{here.label} {name}", rationale=why,
                   entity=Entity(iri=f"local:{_slug(here.label + '-' + name)}",
                                 label=f"{here.label} {name}", gloss=why))
            for name, why in facets
        ]
        # Reuse of an entity already in the reading is the interesting case —
        # it must come back as ONE node — so offer one when there is one.
        others = [e for e in known if e.iri != here.iri]
        if others:
            e = self._rng.choice(others)
            options.append(Option(
                kind="A", label=f"back to {e.label}",
                rationale="an entity already in this reading, used again",
                entity=e))
        return StepProposal(
            kind="A",
            prompt=f"{here.label} can be understood several ways — which do you take?",
            options=options)

    def _kind_b(self, here: Entity) -> StepProposal:
        """A relation understood several ways -> reflection."""
        options = []
        for qa, ans in self._rng.sample(_RELATIONS, 3):
            options.append(Option(
                kind="B", label=f"{qa} vs {ans}",
                rationale=f"holds {qa} against {ans} over {here.label}",
                entity_a=Entity(iri=f"local:{_slug(qa)}", label=qa,
                                gloss="the question side"),
                entity_b=Entity(iri=f"local:{_slug(ans)}", label=ans,
                                gloss="the answer side")))
        return StepProposal(
            kind="B",
            prompt=f"the relation at {here.label} reads several ways — "
                   f"which question against which answer?",
            options=options)

    def _kind_c(self, here: Entity) -> StepProposal:
        """How this place was reached -> contraction option 2."""
        options = [
            Option(kind="C", label=name, rationale=why,
                   entity=Entity(iri=f"local:{_slug(name)}", label=name,
                                 gloss=why))
            for name, why in self._rng.sample(_PREDECESSORS, 3)
        ]
        return StepProposal(
            kind="C",
            prompt=f"how did you reach {here.label}?",
            options=options)
