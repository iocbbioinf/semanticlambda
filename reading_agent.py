"""Delegation to Claude Code — the source of entities, relations and options.

The terminal app owns the CALCULUS (notes/reading_desc, notes/reading_alg); this
module owns everything the calculus cannot derive on its own: which entities are
relevant to the user's query, which relations hold between them, and which of the
readings of an entity or a relation are worth offering.

    app          decides WHICH interaction step is available (A / B / C) and
                 applies it to R = (G(t), Pr)
    this module  proposes WHAT the options are at that place

ENTITIES ARE NOT KG NODES. "By entity here I don't mean specifically entities in
the KG". An entity is a VARIABLE of the reading's term, identified by a slug id
and carrying a human label. Relations are likewise proposed rather than looked
up, so LEGAL_CONTRACT's arrow test (reading_alg §3.1) is answered by the agent
instead of by an RDF graph.

AN ENTITY EXISTS INDEPENDENTLY OF A QUERY. A query is the OCCASION on which one
is proposed, never what it belongs to: the same entity may be reached from any
number of queries, and the store that holds it is global and persistent
(`entity_store`, data/entities.json) for exactly that reason. So an entity is
never "this query's entity" — which is why identity may be shared across
queries while CONTEXT may not (one `claude` session per query, see below), and
why nothing in `EntityStore` records which query first named a thing.

WHO ANSWERS IS PLUGGABLE. This module owns WHAT is asked (the prompts below)
and what the answer MEANS (the decoders); a TRANSPORT owns getting one
schema-conforming dict back from some delegate. `reading_transport` provides
`ClaudeCLITransport` (`claude -p --json-schema`, the default) and
`OpenAITransport` (the OpenAI API, structured outputs); anything with
`invoke(prompt, schema) -> dict` will do. Every reply is machine checked
against a schema before it reaches the calculus whichever transport is in
force.

ONE SESSION PER QUERY. A new query calls `new_session()` and starts from an
empty context, since the senses settled for one query are not senses of the
next. HOW that context is kept is the transport's business and differs by
provider — the CLI resumes a server-side session, the OpenAI transport carries
the message list itself — so the cost of a long interaction differs too; see
`reading_transport`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from reading_transport import (AgentError, ClaudeCLITransport, DEFAULT_TIMEOUT,
                               Transport)

DEFAULT_MODEL = ClaudeCLITransport.DEFAULT_MODEL

# `AgentError` is re-exported: it was raised from here before the transport was
# split out, and `reading_browser` and the tests still catch it by that name.
__all__ = ["AgentError", "Entity", "Option", "StepProposal", "ReadingAgent",
           "DEFAULT_MODEL", "DEFAULT_TIMEOUT"]


# ── what the agent returns ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Entity:
    """A variable of the reading — reading_alg §0 `Entity`.

    `iri` is a stable id (a slug, namespaced locally) rather than a KG IRI; the
    calculus only ever compares these for equality and uses them as the `iri` of
    a `LamVar`, so nothing downstream requires them to resolve.
    """
    iri: str
    label: str
    gloss: str = ""

    def short(self) -> str:
        return self.label or self.iri


@dataclass(frozen=True)
class Option:
    """One way of understanding an entity or a relation.

    `kind` says which interaction step this option belongs to, and therefore how
    the app must apply it:

        "A"  an entity understood one of several ways   -> contraction, option 1
        "B"  a relation understood one of several ways  -> reflection
        "C"  how the current place was REACHED          -> contraction, option 2

    For A and C, `entity` is the operand. For B, `entity_a`/`entity_b` are the
    two relations reflected against one another (question and answer).
    """
    kind: str
    label: str
    rationale: str = ""
    entity: Optional[Entity] = None
    entity_a: Optional[Entity] = None
    entity_b: Optional[Entity] = None
    # set by the app, not the agent: an already-created closed reading offered as
    # an option-1 operand (reading_alg §3.1 — a closed reading may be what the
    # user answers WITH).
    reading_name: Optional[str] = None


@dataclass
class StepProposal:
    """What the agent offers at one place of one reading."""
    kind: str                       # "A" | "B" | "C" | "none"
    prompt: str = ""
    options: list[Option] = field(default_factory=list)
    note: str = ""


# ── schemas ───────────────────────────────────────────────────────────────────

_ENTITY_PROPS = {
    "id": {"type": "string",
           "description": "short lowercase slug, a-z0-9 and hyphens only"},
    "label": {"type": "string", "description": "human-readable name"},
    "gloss": {"type": "string", "description": "one short clause of meaning"},
}

_SEED_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {"type": "object", "properties": _ENTITY_PROPS,
                      "required": ["id", "label"]},
        },
    },
    "required": ["entities"],
}

_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["A", "B", "C", "none"]},
        "prompt": {"type": "string",
                   "description": "the question to put to the user"},
        "note": {"type": "string"},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "rationale": {"type": "string"},
                    "entity": {"type": "object", "properties": _ENTITY_PROPS},
                    "entity_a": {"type": "object", "properties": _ENTITY_PROPS},
                    "entity_b": {"type": "object", "properties": _ENTITY_PROPS},
                },
                "required": ["label"],
            },
        },
    },
    "required": ["kind", "options"],
}


# ── the delegate ──────────────────────────────────────────────────────────────


class ReadingAgent:
    """Turns a delegate's structured replies into calculus material.

    The delegate itself is a TRANSPORT (`reading_transport`): this class owns
    the prompts, the schemas and the decoding, and knows nothing about who
    answers. Construct it with a transport, or with none and it builds the
    default `ClaudeCLITransport` — so `ReadingAgent(model="opus")` still means
    what it always did.
    """

    def __init__(self, model: Optional[str] = None,
                 timeout: int = DEFAULT_TIMEOUT,
                 cwd: Optional[str] = None,
                 session: bool = True,
                 transport: Optional[Transport] = None) -> None:
        self.transport: Transport = transport or ClaudeCLITransport(
            model=model or DEFAULT_MODEL, timeout=timeout, cwd=cwd,
            session=session)

    # The browser's /cost and /time lines, `clarify_plan`'s per-plan cost
    # accounting and `ReadingSession` all read these off the agent, as they did
    # when it WAS the transport. They stay readable there rather than making
    # every call site learn about `.transport`.
    @property
    def model(self) -> str:
        return self.transport.model

    @property
    def last_cost_usd(self) -> float:
        return self.transport.last_cost_usd

    @property
    def total_cost_usd(self) -> float:
        return self.transport.total_cost_usd

    @property
    def last_wall_s(self) -> float:
        return self.transport.last_wall_s

    @property
    def last_api_s(self) -> float:
        return self.transport.last_api_s

    @property
    def last_duration_s(self) -> float:
        return self.transport.last_duration_s

    @property
    def total_wall_s(self) -> float:
        return self.transport.total_wall_s

    @property
    def calls(self) -> int:
        return self.transport.calls

    def new_session(self) -> None:
        """Drop the context, so the NEXT call starts from an empty one.

        A new query must not inherit the previous query's clarifications: the
        senses settled there are not senses of this one.
        """
        self.transport.new_session()

    # ── transport ─────────────────────────────────────────────────────────

    def _invoke(self, prompt: str, schema: dict) -> dict:
        """Ask the transport. `clarify_plan` calls this directly, by design:
        the clarification phase writes its own prompts and schemas but wants
        the same delegate and the same cost accounting."""
        return self.transport.invoke(prompt, schema)

    # ── decoding ──────────────────────────────────────────────────────────

    @staticmethod
    def _entity(d: Optional[dict], seen: dict[str, Entity]) -> Optional[Entity]:
        """Decode one entity, REUSING an id already minted in this reading.

        Entity reuse is the whole point of the registry in `reading_state`: the
        same entity used twice must be ONE node of the sharing graph. Here we
        only make sure the same id keeps the same label; the term-level sharing
        is `EntityRegistry`'s job.
        """
        if not isinstance(d, dict):
            return None
        raw = (d.get("id") or "").strip()
        if not raw:
            return None
        # The delegate is shown ids in their full `local:x` form and often echoes
        # them back that way. Strip the namespace before slugifying, or reusing an
        # entity mints `local:local-x` instead and the registry sees a NEW entity —
        # which would silently break the sharing that makes reuse one node.
        raw = raw.split(":", 1)[-1] if raw.lower().startswith("local:") else raw
        slug = "".join(c if (c.isalnum() or c == "-") else "-"
                       for c in raw.lower()).strip("-")
        if not slug:
            return None
        if slug in seen:
            return seen[slug]
        ent = Entity(iri=f"local:{slug}",
                     label=(d.get("label") or raw).strip(),
                     gloss=(d.get("gloss") or "").strip())
        seen[slug] = ent
        return ent

    # ── the two calls the app makes ───────────────────────────────────────

    def seed_entities(self, query: str, limit: int = 6) -> list[Entity]:
        """Entities to start readings from — the initial step's material (§2)."""
        prompt = (
            "You are the semantic backend of a knowledge-reading tool.\n"
            f"The user's query is:\n\n{query}\n\n"
            f"Propose up to {limit} ENTITIES that this query is about. An entity "
            "is a thing one could stand at while reading: a concept, an object, "
            "a process, an agent. Not a sentence, not a relation, not a question."
            "\n\nOrder them so the most central comes first."
        )
        data = self._invoke(prompt, _SEED_SCHEMA)
        seen: dict[str, Entity] = {}
        out = []
        for d in data.get("entities") or []:
            ent = self._entity(d, seen)
            if ent is not None:
                out.append(ent)
        if not out:
            raise AgentError("the delegate proposed no entities for this query")
        return out[:limit]

    def propose_step(self, query: str, term_text: str, here: Entity,
                     history: list[str], reached_from: Optional[str],
                     known: list[Entity],
                     allow_c: bool) -> StepProposal:
        """Propose the next interaction step at `here`.

        The agent chooses WHICH of the three kinds applies; the app decides what
        to do with it. `allow_c` is False at the root of a reading, where there is
        no "how did I get here" to ask about — nothing precedes the initial step.
        """
        known_txt = ", ".join(f"{e.label} [{e.iri}]" for e in known) or "(none yet)"
        hist_txt = "\n".join(f"  {h}" for h in history[-8:]) or "  (first step)"
        kinds = ["A", "B"] + (["C"] if allow_c else [])
        data = self._invoke(
            "You are the semantic backend of a knowledge-reading tool. The user "
            "reads a knowledge graph by building a term, standing at one entity "
            "at a time.\n\n"
            f"USER'S QUERY:\n{query}\n\n"
            f"THE READING SO FAR (a lambda term, no abstractions):\n  {term_text}\n\n"
            f"STEPS TAKEN:\n{hist_txt}\n\n"
            f"THE USER IS STANDING AT: {here.label} [{here.iri}]"
            + (f"\nREACHED FROM: {reached_from}" if reached_from else "")
            + f"\n\nENTITIES ALREADY IN THIS READING (reuse these ids when you mean "
            f"the same thing):\n  {known_txt}\n\n"
            "Propose ONE interaction step, of exactly one kind:\n"
            "  A — the entity the user stands at can be UNDERSTOOD IN SEVERAL "
            "WAYS. Each option is a different entity to move on to. Give the "
            "option's `entity`.\n"
            "  B — the RELATION between the current entity and another can be "
            "understood in several ways. Each option gives TWO entities, "
            "`entity_a` (the question) and `entity_b` (the answer), to be held "
            "against one another.\n"
            + ("  C — there are several ways the user might have REACHED this "
               "place. Each option's `entity` is a candidate predecessor.\n"
               if allow_c else
               "  (kind C is NOT available here: this is the start of the "
               "reading, so nothing precedes it.)\n")
            + "\nCHOOSE THE KIND BY WHAT IS ACTUALLY AMBIGUOUS HERE — do not "
            "default to A. Prefer B when the interesting ambiguity is in a "
            "RELATION rather than in the entity: when this place answers two "
            "different questions at once, or when two readings of the same link "
            "are both worth holding. Prefer C when what is unclear is which "
            "earlier thing LED HERE. Use A only when the entity itself is what "
            "splits.\n"
            "\nOnly propose a step where the options are genuinely MULTIPLE, "
            "REASONABLE and RELEVANT to the query. Do not manufacture a choice "
            "to keep going: if this place is exhausted, or the only options left "
            "would be arbitrary or repeat what the reading already says, answer "
            "with kind \"none\" and no options.\n"
            "Give 2 to 4 options, each with a short label and a one-clause "
            "rationale. `prompt` is the question to put to the user.\n"
            f"Allowed kinds here: {', '.join(kinds)}, or none.",
            _STEP_SCHEMA,
        )

        kind = (data.get("kind") or "none").strip().upper()
        if kind == "NONE" or kind not in ("A", "B", "C"):
            return StepProposal(kind="none", note=data.get("note", ""))
        if kind == "C" and not allow_c:
            return StepProposal(kind="none", note="C proposed but not available")

        seen = {e.iri.split(":", 1)[-1]: e for e in known}
        options: list[Option] = []
        for d in data.get("options") or []:
            label = (d.get("label") or "").strip()
            if not label:
                continue
            if kind == "B":
                ea = self._entity(d.get("entity_a"), seen)
                eb = self._entity(d.get("entity_b"), seen)
                if ea is None or eb is None:
                    continue
                options.append(Option(kind="B", label=label,
                                      rationale=(d.get("rationale") or "").strip(),
                                      entity_a=ea, entity_b=eb))
            else:
                ent = self._entity(d.get("entity"), seen)
                if ent is None:
                    continue
                options.append(Option(kind=kind, label=label,
                                      rationale=(d.get("rationale") or "").strip(),
                                      entity=ent))

        # "multiple reasonable ways" — a single option is not a choice.
        if len(options) < 2:
            return StepProposal(kind="none",
                                note="fewer than two viable options")
        return StepProposal(kind=kind, prompt=(data.get("prompt") or "").strip(),
                            options=options[:4], note=(data.get("note") or "").strip())
