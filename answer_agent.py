"""The final answer — the query, read through its interaction, put to a model.

This is the last step of the loop and the one the old app never had: its
`resume` printed the reading and stopped. Every clarification prompt in the
project says "do NOT answer the query", and rightly — clarifying and answering
are different jobs. This module does the second one, once the first is finished.

WHAT THE INTERACTION IS FOR. The query alone is what the user could say. The
interaction is what they MEANT, worked out one unclear point at a time: for each
point, the question that was put and the answer they chose. So the prompt is not
"here is a query" but "here is a query, and here is what its author settled about
it" — which is the whole value of having read it.

WHAT IS SENT, AND WHY IN THIS ORDER
  1. the query, verbatim
  2. the clarifications: point -> question -> answer, in the order they happened
  3. what was left unsettled (the lambdaList), named as open
  4. the term, for the reader who wants the structure

(3) matters as much as (2). A skipped or resumed point is not a gap in the
record — it is a fact about the query: the user declined to narrow it there, so
the answer should hold that part open rather than silently picking a reading.
The prompt says so explicitly.

THE DELEGATE IS AN INTERFACE. `Answerer` is anything with `answer(...) -> str`.
`TransportAnswerer` drives a real model through the existing `Transport`
protocol (`claude -p`, the OpenAI API) — unchanged, reused, not duplicated.
`EchoAnswerer` is the default in tests: it renders the prompt deterministically
and calls nothing, so the whole path runs for free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from interaction_store import QueryRecord


# ── the prompt ────────────────────────────────────────────────────────────


CASE_NAMES = {
    1: "clarified by choosing what it refers to",
    2: "clarified by naming the question it answers",
    3: "clarified as a question-and-answer pair",
}


def build_prompt(record: QueryRecord, include_term: bool = True) -> str:
    """The prompt: the original query plus the interaction that read it."""
    lines: list[str] = []
    lines.append("A user asked the question below. Before answering, they went "
                 "through a clarification process: for each unclear point in "
                 "their question, they were asked what they meant and chose an "
                 "answer. That record is given after the question.")
    lines.append("")
    lines.append("THE QUESTION")
    lines.append(f"  {record.query}")
    lines.append("")

    names = {iri: meta.get("label", iri) for iri, meta in record.entities.items()}

    steps = [(i, s) for i in record.interactions for s in i["steps"]]
    if steps:
        lines.append("WHAT THEY CLARIFIED")
        for inter, s in steps:
            point = s.get("point") or inter.get("subquery") or ""
            head = f"“{point}”" if point else "(a point in the question)"
            lines.append(f"  {head}")
            if s.get("question"):
                lines.append(f"    asked:  {s['question']}")
            lines.append(f"    chose:  {s.get('answer', '')}")
            why = CASE_NAMES.get(s.get("case"))
            if why:
                lines.append(f"    ({why})")
        lines.append("")

    if record.lambda_list:
        open_names = [names.get(iri, iri) for iri in record.lambda_list]
        lines.append("WHAT THEY LEFT OPEN")
        lines.append("  " + ", ".join(open_names))
        lines.append("  They did not settle these. Do not silently pick one "
                     "reading — answer so that what they left open stays open, "
                     "or say plainly what the answer depends on.")
        lines.append("")

    if include_term and record.interactions:
        lines.append("THE READING, AS A TERM")
        for i in record.interactions:
            lines.append(f"  {i['name']}: {_term_text(i['term'], names)}")
        lines.append("")

    lines.append("Answer the question as it was clarified, not as it was first "
                 "worded. Do not restate the clarification record back to them; "
                 "use it. If the clarifications rule out a reading you would "
                 "otherwise have given, follow them.")
    return "\n".join(lines)


def _term_text(d: dict, names: dict[str, str]) -> str:
    """A term document as readable text, entities by name."""
    kind = d.get("type")
    if kind == "var":
        return names.get(d.get("iri", ""), d.get("label") or d.get("iri", "?"))
    if kind == "app":
        f = _term_text(d["func"], names)
        a = _term_text(d["arg"], names)
        ft = d.get("func_type")
        if ft:
            # the retype: what the function is ASKING at this application
            return f"({f} [as {names.get(ft, ft)}] · {a})"
        return f"({f} · {a})"
    if kind == "fan":
        p = _term_text(d["principal"], names)
        g = names.get(d.get("grey_cast") or "", d.get("grey_cast") or "?")
        b = names.get(d.get("black_cast") or "", d.get("black_cast") or "?")
        return f"▽{p}[{g} | {b}]"
    if kind == "abs":
        v = d["var"]
        vn = names.get(v.get("iri", ""), v.get("label", "?"))
        return f"λ{vn}.{_term_text(d['body'], names)}"
    if kind == "hole":
        return "▽"
    return "?"


# ── the delegate ──────────────────────────────────────────────────────────


class Answerer(Protocol):
    """Whoever produces the final answer."""

    def answer(self, record: QueryRecord) -> str:
        ...


class EchoAnswerer:
    """The default: renders the prompt, calls nothing.

    Not a toy — it is what makes the whole path testable and what a dev
    checkout runs without an API key or a token of spend.
    """

    def __init__(self, include_term: bool = True) -> None:
        self.include_term = include_term
        self.last_prompt = ""

    def answer(self, record: QueryRecord) -> str:
        self.last_prompt = build_prompt(record, include_term=self.include_term)
        return self.last_prompt


_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string",
                   "description": "the answer to the user's question, as prose"},
    },
    "required": ["answer"],
}


class TransportAnswerer:
    """A real model, over the existing `Transport` protocol.

    The transports return a dict against a schema rather than free text, so the
    answer is asked for as a one-field object. That reuses `ClaudeCLITransport`
    and `OpenAITransport` exactly as they are, instead of adding a second way to
    reach the same providers.
    """

    def __init__(self, transport, include_term: bool = True) -> None:
        self.transport = transport
        self.include_term = include_term
        self.last_prompt = ""

    def answer(self, record: QueryRecord) -> str:
        self.last_prompt = build_prompt(record, include_term=self.include_term)
        data = self.transport.invoke(self.last_prompt, _ANSWER_SCHEMA)
        return (data.get("answer") or "").strip()


# ── the step ──────────────────────────────────────────────────────────────


@dataclass
class Answered:
    record: QueryRecord
    answer: str
    prompt: str = ""


def answer_query(record: QueryRecord, answerer: Optional[Answerer] = None,
                 store=None) -> Answered:
    """Answer a finished query, and record the answer beside the interaction.

    Saving the answer WITH the interaction is the point: the record then holds
    what was asked, what it was taken to mean, and what was said back — the
    three together are the thing worth keeping.
    """
    ans = answerer if answerer is not None else EchoAnswerer()
    text = ans.answer(record)
    record.answer = text
    if store is not None:
        store.save(record)
    return Answered(record=record, answer=text,
                    prompt=getattr(ans, "last_prompt", ""))
