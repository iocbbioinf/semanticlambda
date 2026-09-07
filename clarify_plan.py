"""The CLARIFICATION PHASE — one batched call, then the interaction runs locally.

The first phase of reading a query is not answering it: it is settling WHAT WAS
ASKED. Each interaction step resolves one ambiguity in the user's own words, and
the entities of the reading are the ways those words can be understood. The
reading R built here IS the disambiguated query.

"SENSE" IS NOT A SECOND KIND OF OBJECT. The `sense` fields below hold ordinary
`Entity` values — the same dataclass, the same global store, the same node of the
sharing graph. §2 already makes an entity BE a type ("the most general question of
its type"), and `reading_agent` already mints entities freely from the query
rather than from a KG. The word only names WHAT an entity denotes in this phase:
a way of reading the user's words, instead of a thing in the domain. The calculus
never inspects the difference, which is why this phase needed no new operation.
(The genuinely distinct neighbour is a SUBTYPE named by a question, §2's A1 < A —
created by `ask_question`, not here.)

ONE STEP PER CALL, AND ONE MODEL. Each call asks for exactly ONE point, from
where the user now stands, and the phase runs on whichever model the user chose
throughout.

WHY NOT BATCH. An earlier version asked for all the points up front — one call,
cheapest in total. It was much worse to use: TIME TO FIRST QUESTION is what the
user waits for, and generation is serial, so latency tracks OUTPUT SIZE. Measured
on the 5-HT2C query: six points with their options is ~2000 output tokens and
~36s of silence; one point is ~420 tokens and ~6s. Asking one at a time costs the
same per step and shows the first question six times sooner.

The second reason is better questions. A point planned before the user chose
anything has its options written in ignorance of that choice, so it cannot ask
"given that you meant binding affinity, does the BBB condition mean X or Y". The
options stay VALID — a point's candidates are a property of the query text, which
is why step C needs no knowledge of the path taken (see below) — but they are
ranked and phrased for a reader who has not yet decided anything. Asking from the
current position buys a sharper question for the same wait.

STEP C IS NOT RETROSPECTIVE. Verified against the calculus: from `A · B`,
applying option 2 with operand A yields `A · (A · B)` — the type stays B, the
user stays at B, and A is ONE SHARED NODE with two occurrences. So the same point
may be re-asked after the reading has grown (arriving at B changes what A was,
and the outer application records the further determination), and a LATER,
unrelated point may take a point of view that maps back to an entity already in
the reading. Neither needs knowledge of the user's future choices, which is why
both can be planned in advance.

This module owns NO calculus. It produces `StepProposal`/`Option` values exactly
as `ReadingAgent` does, so `ReadingSession.apply` applies them unchanged: A ->
contraction option 1, B -> reflection, C -> contraction option 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from reading_agent import (AgentError, Entity, Option, StepProposal,
                           ReadingAgent)

# How many points to ask for. Generous on purpose: the cost is the process, not
# the tokens, so covering branches the user never walks is nearly free, while a
# point the plan MISSED costs a whole extra call to recover.
DEFAULT_POINTS = 6


# ── the plan ──────────────────────────────────────────────────────────────────


@dataclass
class PlannedOption:
    """One way of understanding a point of the query.

    `label` is how the point is understood, IN THE USER'S OWN TERMS — that is
    what the default view shows. `sense`/`sense_b` are the ENTITIES it maps to
    (ordinary `Entity` values, see the module docstring), shown only under
    --verbose: the mapping is calculus, the understanding is the choice.
    """
    label: str
    sense: Entity                       # the entity this understanding maps to
    rationale: str = ""
    sense_b: Optional[Entity] = None    # kind B only: the answer side's cast


@dataclass
class PlannedPoint:
    """One point of the query that can be understood several ways.

    `kind` says which reading step resolves it:
        "A"  the point itself splits            -> contraction option 1
        "B"  a RELATION between points splits   -> reflection
        "C"  the POINT OF VIEW onto here splits -> contraction option 2

    `at_sense` is the sense the plan expects the user to be standing at when this
    point becomes live; it is a HINT for ordering, never a precondition —
    `ClarificationPlan.proposal_for` will serve any point whose options are
    unspent, because a point's senses do not depend on the path taken.
    """
    quote: str                          # the words of the query at issue
    kind: str
    question: str                       # the question to put to the user
    options: list[PlannedOption] = field(default_factory=list)
    at_sense: Optional[str] = None      # iri, a hint only
    spent: bool = False


@dataclass
class ClarificationPlan:
    """Everything the one call produced, plus what the interaction has spent."""
    query: str
    root: Entity                        # the query mapped to one entity (init)
    points: list[PlannedPoint] = field(default_factory=list)
    cost_usd: float = 0.0
    calls: int = 1

    # ── serving the interaction, locally ──────────────────────────────────

    def open_points(self, kinds: Optional[set[str]] = None) -> list[PlannedPoint]:
        return [p for p in self.points
                if not p.spent and (kinds is None or p.kind in kinds)]

    def proposal_for(self, here: Optional[Entity], allow_c: bool,
                     allow_b: bool = True) -> Optional[StepProposal]:
        """The next planned step, preferring points meant for where we stand.

        A point is served whether or not its `at_sense` matches: an entity is a
        property of the QUERY, so a planned point stays valid wherever the user
        happens to be standing. PLAN ORDER WINS. The delegate is asked to rank
        points so the ones that most change a correct answer come first, and that
        ranking is better information than the hint: `at_sense` is a guess about
        where the user will be, made before they chose anything, so letting it
        jump a point ahead of higher-ranked ones just reorders the phase by the
        delegate's least reliable field. The hint is used only to break ties
        BETWEEN points the plan ranked equally.
        """
        kinds = {"A", "C"} if not allow_b else {"A", "B", "C"}
        if not allow_c:
            kinds.discard("C")
        cands = self.open_points(kinds)
        if not cands:
            return None

        here_iri = here.iri if here is not None else None
        cands.sort(key=lambda p: (self.points.index(p), p.at_sense != here_iri))
        pt = cands[0]

        options = []
        for o in pt.options:
            if pt.kind == "B":
                if o.sense_b is None:
                    continue
                options.append(Option(kind="B", label=o.label,
                                      rationale=o.rationale,
                                      entity_a=o.sense, entity_b=o.sense_b))
            else:
                options.append(Option(kind=pt.kind, label=o.label,
                                      rationale=o.rationale, entity=o.sense))
        # "multiple reasonable ways" — a single option is not a choice (§4).
        if len(options) < 2:
            pt.spent = True
            return self.proposal_for(here, allow_c, allow_b)
        return StepProposal(kind=pt.kind, prompt=pt.question,
                            options=options[:4],
                            note=f"clarifying: “{pt.quote}”")

    def spend(self, prop: StepProposal) -> None:
        """Mark the point this proposal came from as resolved."""
        for p in self.points:
            if not p.spent and p.question == prop.prompt:
                p.spent = True
                return

# ── schemas ───────────────────────────────────────────────

# The label is deliberately terse, so THE GLOSS IS WHERE THE MEANING LIVES — it
# is what the reading prints after a bare name. It must therefore be REQUIRED:
# left optional, the delegate omitted it most of the time (70 of 111 stored
# entities had none), and readings rendered a name with nothing after it.
_SENSE = {
    "type": "object",
    "properties": {
        "id": {"type": "string",
               "description": "short lowercase slug, a-z0-9 and hyphens only"},
        "label": {"type": "string",
                  "description": "at most 4 words — a name, not a description"},
        "gloss": {"type": "string",
                  "description": "REQUIRED, at most 12 words: one clause saying "
                                 "what this means. The label is only a name, so "
                                 "this is what the reader is shown. Never repeat "
                                 "the label; never leave it empty"},
    },
    "required": ["id", "label", "gloss"],
}

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "root": dict(_SENSE, description="the query as a whole, mapped to one sense"),
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string",
                              "description": "the exact words of the query at issue"},
                    "kind": {"type": "string", "enum": ["A", "B", "C"]},
                    "question": {"type": "string",
                                 "description": "the question to put to the user"},
                    "at_sense": {"type": "string",
                                 "description": "id of the sense the user is "
                                                "expected to stand at; may be omitted"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string",
                                          "description": "how this point is understood, "
                                                         "in the user's own terms"},
                                "rationale": {"type": "string"},
                                "sense": _SENSE,
                                "sense_b": dict(
                                    _SENSE,
                                    description="kind B only: the ANSWER side; "
                                                "`sense` is the question side"),
                            },
                            "required": ["label", "sense"],
                        },
                    },
                },
                "required": ["quote", "kind", "question", "options"],
            },
        },
    },
    "required": ["root", "points"],
}


_PROMPT = """You are the CLARIFICATION phase of a query-reading tool. Your job is \
NOT to answer the query. It is to find where the query is AMBIGUOUS, so the user \
can settle what they meant before anything is answered.

THE QUERY:
{query}

First, map the query AS A WHOLE to one entity — a short slug and label naming what \
this query is about. That is `root`.

A `label` IS A NAME, NOT A DESCRIPTION: at most about four words, the way a column \
heading or a menu item is written. It is printed inline in the reading, in pointer \
lists and in option lines, so a sentence there wrecks the display. Put the \
explanation in `gloss`, which is REQUIRED and is what the reader is actually shown \
to understand the name — one clause, never a repeat of the label, never empty.

Then find up to {n} POINTS in the query that can be understood in several ways. \
Be generous: a point the user never reaches costs nothing, a point you MISS cannot \
be recovered. For each point give the `quote`, the `question` to put to the user, \
and 2-4 `options`.

`quote` MUST BE THE SHORTEST RUN OF THE QUERY'S OWN WORDS THAT LOCATES THE \
AMBIGUITY — a few words, copied verbatim, the phrase a reader would underline. It \
is shown to the user as "in your query: …", so quoting a whole clause tells them \
nothing about WHERE the doubt is. Even when a point concerns how two parts of the \
query relate (kind B), quote only the part in question, not the span between them: \
"are hydrophobic", never "molecules that can pass the blood-brain barrier, are \
hydrophobic and have been experimentally shown to interact".

Every option has a `label` saying HOW THE POINT IS UNDERSTOOD IN THAT OPTION, in \
the user's own terms — not jargon, not a database name — and a `sense` it maps to: \
a short slug plus label naming that understanding. Two options MAY map to the same \
sense id if they really are the same understanding reached two ways; reuse the id \
when so, and reuse `root`'s id or an earlier point's sense id whenever the same \
sense recurs.

Each point is one of three kinds. Choose by WHAT is ambiguous:

  A — THE POINT ITSELF can be read several ways. Each option is a different \
sense of those words. Use this for a term, a condition, or a qualifier whose \
meaning is unsettled.

  B — A RELATION between two points of the query can be read several ways. Each \
option gives TWO senses: `sense` is the QUESTION side and `sense_b` the ANSWER \
side, held against one another. Use this when the doubt is how two parts of the \
query bear on each other — whether one restricts the other, whether they are \
independent, whether one is evidence for the other.

  C — THE POINT OF VIEW onto the query's sense so far can be read several ways: \
there are several vantages from which this place could be understood, and each \
option is one of them. Each option's `sense` is that vantage. Use this when what \
is unclear is under WHICH READING of an earlier part the current part should be \
taken. A `C` point may take a vantage that is the same sense as an earlier \
point's option — say so by reusing that id.

Set `at_sense` to the id of the sense the user is likely standing at when the \
point becomes live, when there is a natural one; omit it otherwise.

Rank the points so the ones that most change what a correct answer would be come \
FIRST. Prefer ambiguities that would send an answerer down genuinely different \
paths over ones that merely add precision. Do not invent ambiguity: if the query \
is precise on some point, leave that point out."""


# The first call's schema is DELIBERATELY CAPPED: maxItems on points and options
# is what keeps the output small, and output size is the latency.
_FIRST_SCHEMA = {
    "type": "object",
    "properties": {
        "root": dict(_SENSE, description="the query as a whole, as one entity"),
        "points": {
            "type": "array",
            "maxItems": 1,
            "items": _PLAN_SCHEMA["properties"]["points"]["items"],
        },
    },
    "required": ["root", "points"],
}


_FIRST_PROMPT = """You are the CLARIFICATION phase of a query-reading tool. Do NOT \
answer the query.

THE QUERY:
{query}

BE TERSE. Respect every length limit in the schema; no prose outside the fields. \
You are being timed — the user is waiting for this one question.

Give:
  `root`  — the query as a whole, as one entity: a name of at most 4 words plus a \
one-clause gloss.
  `points` — EXACTLY ONE point: the SINGLE most consequential ambiguity in the \
query, the one whose resolution most changes what a correct answer would be. \
Kind "A". `quote` is the shortest run of the query's OWN words that locates it \
(a few words, verbatim). 2-3 `options`, each a way of understanding that point in \
the user's own terms, mapped to a `sense` entity."""


_NEXT_PROMPT = """You are the CLARIFICATION phase of a query-reading tool. Do NOT \
answer the query.

THE QUERY:
{query}

It is about: {root}

THE USER HAS SETTLED, in order:
{have}

THE READING SO FAR (a term; each application is one settled point):
  {term}
THE USER IS STANDING AT: {here}
ENTITIES ALREADY IN THIS READING — reuse these ids when you mean the same thing:
  {known}

BE TERSE. Respect every length limit in the schema; no prose outside the fields. \
You are being timed — the user is waiting for this one question.

Give ONE further point: the next most consequential ambiguity, ASKED FROM WHERE \
THE USER NOW STANDS. What they have already settled changes what is worth asking \
and how — say "given that X was meant, ..." where that is what makes the question \
sharp. Do not re-ask anything above, and do not quote the same words again.

`quote` is the shortest run of the query's OWN words that locates the point \
(a few words, verbatim). 2-3 `options`, each a way of understanding it in the \
user's own terms, mapped to a `sense` entity. Allowed kinds here: {kinds}.
  A — the quoted words themselves read several ways.
  B — how two parts of the query bear on each other; give `sense` (question side) \
and `sense_b` (answer side), and quote only the part in question.
  C — which vantage the current place should be taken under; the vantage may be an \
entity the reading already holds (reuse its id).

If nothing genuinely open remains, return an empty `points` list.

Also repeat `root` unchanged."""


_TOPUP = """You are the CLARIFICATION phase of a query-reading tool. Do NOT answer \
the query.

THE QUERY:
{query}

The user has settled some of it already. Their reading so far, as a term:
  {term}

They are now standing at: {here}
Senses already in the reading (REUSE these ids when you mean the same thing):
  {known}
Points already settled:
{settled}

The remaining ambiguities were enumerated cold, before any of this was chosen, so \
they are ranked for the query in general rather than for where the user now \
stands. Re-rank and, where standing here makes a better reading of some point \
visible, give it: up to {n} points, same format as before, ONLY ones that are \
still genuinely open from HERE. Prefer points whose resolution changes what a \
correct answer would be. If nothing worth asking remains, return an empty \
`points` list."""


# ── building the plan ─────────────────────────────────────────────────────────


class ClarificationPlanner:
    """Wraps a `ReadingAgent` transport to produce and top up a plan.

    The transport is reused as-is, so cost accounting (`last_cost_usd`,
    `total_cost_usd`) and the `--verbose` /cost line report the clarification
    phase exactly as they report any other delegation.
    """

    def __init__(self, agent: ReadingAgent) -> None:
        self.agent = agent

    # ── decoding ──────────────────────────────────────────────────────────

    # A label is printed inline — in the term, in pointer lines, in option rows —
    # so it has to stay a NAME. The prompt asks for that, but a delegate that
    # answers with a sentence anyway must not be able to wreck every line of the
    # display, so the boundary enforces it too.
    MAX_LABEL = 40

    # A quote is shown as "in your query: …" to say WHERE the doubt is, so a
    # whole-clause quote defeats its purpose. The prompt asks for the shortest
    # locating phrase; this clips one that came back long anyway.
    MAX_QUOTE = 60

    @classmethod
    def _clip_quote(cls, text: str) -> str:
        if len(text) <= cls.MAX_QUOTE:
            return text
        words, out = text.split(), []
        for w in words:
            if len(" ".join(out + [w])) > cls.MAX_QUOTE - 1:
                break
            out.append(w)
        return (" ".join(out) + "…") if out else text[:cls.MAX_QUOTE - 1] + "…"

    @classmethod
    def _name_from(cls, text: str) -> str:
        """A short name from long text, cut on a word boundary."""
        words, name = text.replace("—", " ").replace("-", " ").split(), []
        for w in words:
            if len(" ".join(name + [w])) > cls.MAX_LABEL - 1:
                break
            name.append(w)
        return (" ".join(name) + "…") if name else text[:cls.MAX_LABEL - 1] + "…"

    @classmethod
    def _shorten(cls, d: Optional[dict]) -> Optional[dict]:
        """Move an over-long label into the gloss, keeping a short name.

        The label is not the only way long text arrives: `ReadingAgent._entity`
        falls back to the raw ID when no label is given, so a delegate that
        answers with a sentence-length slug and no label would bypass a check
        that looked at `label` alone — which is exactly what a real run did.
        """
        if not isinstance(d, dict):
            return d
        label = (d.get("label") or "").strip()
        if not label:
            # No label: the id becomes one downstream, so name it from the id
            # here (hyphens included, since a slug's words are hyphen-separated).
            raw = (d.get("id") or "").strip()
            raw = raw.split(":", 1)[-1] if raw.lower().startswith("local:") else raw
            if len(raw) > cls.MAX_LABEL:
                d = dict(d)
                d["gloss"] = (d.get("gloss") or "").strip() or raw
                d["label"] = cls._name_from(raw)
            return d
        if len(label) <= cls.MAX_LABEL:
            return d
        d = dict(d)
        # The gloss is where the sentence belongs; keep whichever text is longer
        # if a gloss was given too, since that is the fuller explanation.
        gloss = (d.get("gloss") or "").strip()
        d["gloss"] = label if len(label) > len(gloss) else gloss
        # A name from the head of the sentence: whole words, no mid-word cut.
        d["label"] = cls._name_from(label)
        return d

    def _sense(self, d: Optional[dict], seen: dict[str, Entity]) -> Optional[Entity]:
        """Decode one entity, REUSING an id already minted for this plan.

        Reuse is what makes two options that mean the same thing ONE node of the
        sharing graph (§1), and what lets a `C` point take a vantage that is an
        entity the reading already holds.
        """
        return ReadingAgent._entity(self._shorten(d), seen)

    def _decode(self, data: dict, seen: dict[str, Entity]) -> list[PlannedPoint]:
        points: list[PlannedPoint] = []
        for d in data.get("points") or []:
            kind = (d.get("kind") or "").strip().upper()
            if kind not in ("A", "B", "C"):
                continue
            quote = self._clip_quote((d.get("quote") or "").strip())
            question = (d.get("question") or "").strip()
            if not question:
                continue
            opts: list[PlannedOption] = []
            for od in d.get("options") or []:
                label = (od.get("label") or "").strip()
                sense = self._sense(od.get("sense"), seen)
                if not label or sense is None:
                    continue
                sense_b = (self._sense(od.get("sense_b"), seen)
                           if kind == "B" else None)
                if kind == "B" and sense_b is None:
                    continue
                opts.append(PlannedOption(
                    label=label, sense=sense, sense_b=sense_b,
                    rationale=(od.get("rationale") or "").strip()))
            if len(opts) < 2:
                continue
            at = (d.get("at_sense") or "").strip()
            at_iri = None
            if at:
                hint = self._sense({"id": at, "label": at}, seen)
                at_iri = hint.iri if hint is not None else None
            points.append(PlannedPoint(quote=quote, kind=kind, question=question,
                                       options=opts, at_sense=at_iri))
        return points

    # ── the one call ──────────────────────────────────────────────────────

    # TIME TO FIRST QUESTION is what the user waits for, and generation is
    # SERIAL: the latency of one `claude -p` is driven by how many tokens it must
    # emit, not by how hard the question is. Measured on the 5-HT2C query: six
    # points with their options is ~2000 output tokens and 36s; ONE point is
    # ~420 tokens and 6.5s on haiku. So the phase asks for one point first, shows
    # it, and fetches the rest WHILE THE USER READS — the wait the user actually
    # experiences drops from 36s to ~6s, and the remaining points cost the same
    # as before but off the critical path.
    def plan_first(self, query: str) -> ClarificationPlan:
        """The root plus ONE point — the whole of the first wait.

        ONE MODEL for the whole reading: the delegate's model is whatever the
        user chose, here as everywhere. Mixing models inside one reading would
        mean the first question and the later ones come from different judgement.
        """
        before = self.agent.total_cost_usd
        data = self.agent._invoke(_FIRST_PROMPT.format(query=query),
                                  _FIRST_SCHEMA)
        seen: dict[str, Entity] = {}
        root = self._sense(data.get("root"), seen)
        if root is None:
            raise AgentError("the delegate did not map the query to an entity")
        points = self._decode(data, seen)
        if not points:
            raise AgentError("the delegate found nothing to clarify in this query")
        return ClarificationPlan(query=query, root=root, points=points,
                                 cost_usd=self.agent.total_cost_usd - before)

    def plan_next(self, plan: ClarificationPlan, term_text: str,
                  here: Optional[Entity], known: list[Entity],
                  allow_c: bool) -> int:
        """ONE further point, asked from WHERE THE USER NOW STANDS.

        This is the difference between planning ahead and clarifying: a point
        planned before the user chose anything has options written in ignorance
        of that choice, so it cannot say "given you meant binding affinity, does
        the BBB condition mean X or Y". Asking one step at a time costs the same
        wait per step (~6s either way — the latency is output size, and one point
        is one point) and buys a question that knows what was already settled.

        Returns how many points were added (0 or 1).
        """
        before = self.agent.total_cost_usd
        have = "\n".join(f"  · “{pt.quote}” — {pt.question}"
                          for pt in plan.points) or "  (none yet)"
        known_txt = ", ".join(f"{e.label} [{e.iri}]" for e in known) or "(none)"
        data = self.agent._invoke(
            _NEXT_PROMPT.format(
                query=plan.query, root=plan.root.label, term=term_text,
                here=(here.label if here else "?"), have=have, known=known_txt,
                kinds=("A, B or C" if allow_c else "A or B (not C: nothing "
                       "precedes the start of the reading)")),
            _FIRST_SCHEMA)
        seen = {plan.root.iri.split(":", 1)[-1]: plan.root}
        for e in known:
            seen[e.iri.split(":", 1)[-1]] = e
        added = self._decode(data, seen)
        # Never ask the same words twice — a real run produced `are hydrophobic`
        # as both an A and a B point, which reads as the same question repeated.
        asked = {pt.quote.strip().lower() for pt in plan.points}
        fresh = [pt for pt in added
                 if pt.quote.strip().lower() not in asked
                 and (allow_c or pt.kind != "C")]
        plan.points.extend(fresh[:1])
        plan.cost_usd += self.agent.total_cost_usd - before
        plan.calls += 1
        return len(fresh[:1])

    def plan(self, query: str, n: int = DEFAULT_POINTS) -> ClarificationPlan:
        before = self.agent.total_cost_usd
        data = self.agent._invoke(_PROMPT.format(query=query, n=n), _PLAN_SCHEMA)
        seen: dict[str, Entity] = {}
        root = self._sense(data.get("root"), seen)
        if root is None:
            raise AgentError("the delegate did not map the query to a sense")
        points = self._decode(data, seen)
        if not points:
            raise AgentError("the delegate found nothing to clarify in this query")
        return ClarificationPlan(query=query, root=root, points=points,
                                 cost_usd=self.agent.total_cost_usd - before)
