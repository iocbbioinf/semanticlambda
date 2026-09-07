"""Delegation to Claude Code — the source of entities, relations and options.

The terminal app owns the CALCULUS (notes/reading_desc, notes/reading_alg); this
module owns everything the calculus cannot derive on its own: which entities are
relevant to the user's query, which relations hold between them, and which of the
readings of an entity or a relation are worth offering.

    app          decides WHICH interaction step is available (A / B / C) and
                 applies it to R = (G(t), Pr)
    this module  proposes WHAT the options are at that place

ENTITIES ARE NOT KG NODES. They are minted freely from the query — "by entity
here I don't mean specifically entities in the KG". An entity is a VARIABLE of
the reading's term, identified by a slug id and carrying a human label. Relations
are likewise proposed rather than looked up, so LEGAL_CONTRACT's arrow test
(reading_alg §3.1) is answered by the agent instead of by an RDF graph.

Delegation runs `claude -p` with `--json-schema`, so every reply is machine
checked against a schema before it reaches the calculus. The CLI returns an
envelope whose `result` field holds the payload AS A JSON STRING — hence the
second decode in `_invoke`.

ONE SESSION PER QUERY. All the delegations of one interaction share a single
`claude` session: the first call pins a fresh `--session-id`, later ones
`--resume` it. A new query calls `new_session()` and starts from an empty
context, since the senses settled for one query are not senses of the next.
This is a cost decision as much as a coherence one — a cold `claude -p` pays
full startup (system prompt, tool schemas, no cache warmth) before doing any
work, which on the 5-HT2C query was ~$0.02 of a ~$0.026 call.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_MODEL = "sonnet"
DEFAULT_TIMEOUT = 240


class AgentError(RuntimeError):
    """The delegate could not be reached, or replied with something unusable."""


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
    """Runs `claude -p` and turns its structured replies into calculus material."""

    def __init__(self, model: str = DEFAULT_MODEL,
                 timeout: int = DEFAULT_TIMEOUT,
                 cwd: Optional[str] = None,
                 session: bool = True) -> None:
        self.model = model
        self.timeout = timeout
        self.cwd = cwd
        self.last_cost_usd = 0.0
        self.total_cost_usd = 0.0
        # ONE SESSION PER QUERY. The first call pins a fresh session id; every
        # later call in the same interaction RESUMES it, so the query and the
        # choices already made are context the delegate still holds, and the
        # per-process overhead that dominates a cold `claude -p` is paid once.
        # Measured on the 5-HT2C query: a cold call reads ~24k cache tokens and
        # costs ~$0.02 before any reasoning; a resumed one reads ~28k and writes
        # 63, costing ~$0.006 — the difference IS the startup.
        self.use_session = session
        self.session_id: Optional[str] = None
        self.calls = 0
        # How long the last delegation took, and where the time went. The CLI
        # reports its own `duration_ms`/`duration_api_ms`, so the gap between
        # our wall clock and its api time IS the process startup we pay for
        # spawning `claude -p` at all — worth seeing under --verbose, since it
        # is the reason the clarification phase batches.
        self.last_wall_s = 0.0
        self.last_api_s = 0.0
        self.last_duration_s = 0.0
        self.total_wall_s = 0.0

    def new_session(self) -> None:
        """Drop the session, so the NEXT call starts from an empty context.

        A new query must not inherit the previous query's clarifications: the
        senses settled there are not senses of this one.
        """
        self.session_id = None

    # ── transport ─────────────────────────────────────────────────────────

    def _invoke(self, prompt: str, schema: dict) -> dict:
        exe = shutil.which("claude")
        if exe is None:
            raise AgentError(
                "the `claude` CLI is not on PATH — this app delegates to it")
        cmd = [exe, "-p", prompt,
               "--output-format", "json",
               "--json-schema", json.dumps(schema),
               "--model", self.model]
        # `--session-id` pins a NEW session (it must not already exist);
        # `--resume` continues it. So the first call of a query mints the id and
        # every later one resumes, which is what keeps the interaction in one
        # context. `--no-session-persistence` is deliberately NOT passed: the
        # session has to survive between our separate processes.
        if self.use_session:
            if self.session_id is None:
                self.session_id = str(uuid.uuid4())
                cmd += ["--session-id", self.session_id]
            else:
                cmd += ["--resume", self.session_id]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout,
                stdin=subprocess.DEVNULL, cwd=self.cwd,
            )
        except subprocess.TimeoutExpired:
            raise AgentError(f"the delegate did not answer within "
                             f"{self.timeout}s") from None
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise AgentError(f"`claude -p` failed: {detail[:400]}")

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise AgentError(
                f"delegate reply was not JSON: {proc.stdout[:300]}") from None
        if envelope.get("is_error"):
            raise AgentError(f"delegate reported an error: "
                             f"{str(envelope.get('result'))[:300]}")

        self.last_wall_s = time.monotonic() - t0
        self.total_wall_s += self.last_wall_s
        self.last_api_s = (envelope.get("duration_api_ms") or 0) / 1000.0
        self.last_duration_s = (envelope.get("duration_ms") or 0) / 1000.0

        cost = envelope.get("total_cost_usd") or 0.0
        self.last_cost_usd = cost
        self.total_cost_usd += cost
        self.calls += 1
        # Trust the CLI's own id over ours: a resume may fork (`--fork-session`
        # elsewhere, or a session the CLI declines to reuse), and following the
        # id it reports keeps the chain intact instead of resuming a dead one.
        got = envelope.get("session_id")
        if self.use_session and isinstance(got, str) and got:
            self.session_id = got

        # The schema-conforming payload arrives as a STRING in `result`.
        payload = envelope.get("result")
        if isinstance(payload, dict):
            return payload
        try:
            return json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            raise AgentError(
                f"delegate payload was not the requested JSON object: "
                f"{str(payload)[:300]}") from None

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
