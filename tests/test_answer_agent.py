"""Tests for answer_agent.py — the final answer step.

Run from the project root:  .venv/bin/python tests/test_answer_agent.py

No model is called: EchoAnswerer renders the prompt, and a FakeTransport stands
in for a real one to prove TransportAnswerer drives the existing protocol.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from answer_agent import (
    Answered, EchoAnswerer, TransportAnswerer, answer_query, build_prompt,
)
from interaction_engine import Entity, Option, Proposal, QuerySession
from interaction_store import InteractionStore, record_from_session

FAILED: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  {name:<48} OK")
    else:
        print(f"  {name:<48} FAIL  got={got!r} want={want!r}")
        FAILED.append(name)


def ok(name, cond):
    check(name, bool(cond), True)


ASPIRIN = Entity("aspirin", "aspirin", "a painkiller")
COX = Entity("cox", "COX enzymes")
PAIN = Entity("pain", "pain relief")
INFL = Entity("infl", "inflammation")


class Scripted:
    def __init__(self, seeds, proposals):
        self._seeds, self._proposals = seeds, list(proposals)

    def seeds(self, query):
        return self._seeds

    def propose(self, *a, **k):
        return self._proposals.pop(0) if self._proposals else Proposal(case=0)


def finished_record(proposals, do=None, user="marek"):
    s = QuerySession("how does aspirin work",
                     Scripted([("aspirin", ASPIRIN)], proposals), user=user)
    s.start()
    s.open(ASPIRIN)
    if do:
        do(s)
    if s.current is not None:
        s.close()
    return s, record_from_session(s)


# ── the prompt ────────────────────────────────────────────────────────────
print("=== the prompt carries the query and the clarifications ===")

p1 = Proposal(case=1, point="how does aspirin work", question="work how?",
              retype=COX,
              options=[Option("by blocking COX", entity=COX),
                       Option("by relieving pain", entity=PAIN)])


def take_first(s):
    prop = s.propose()
    s.apply(prop, prop.options[0])


s, rec = finished_record([p1], do=take_first)
prompt = build_prompt(rec)

ok("the query is present, verbatim", "how does aspirin work" in prompt)
ok("the clarification section is there", "WHAT THEY CLARIFIED" in prompt)
ok("the point is quoted", "how does aspirin work" in prompt)
ok("instructed to use, not restate", "not as it was first worded" in prompt)

# an interaction with NO steps has no clarification section — there is
# nothing to report, and a heading over nothing would mislead the model
_, bare = finished_record([])
ok("no clarification section when nothing was clarified",
   "WHAT THEY CLARIFIED" not in build_prompt(bare))

# with a step actually applied
s2 = QuerySession("how does aspirin work",
                  Scripted([("aspirin", ASPIRIN)], [p1]), user="marek")
s2.start()
s2.open(ASPIRIN)
prop = s2.propose()
s2.apply(prop, prop.options[0])
s2.close()
rec2 = record_from_session(s2)
prompt2 = build_prompt(rec2)
ok("the question put is included", "work how?" in prompt2)
ok("the answer chosen is included", "by blocking COX" in prompt2)
ok("the case is explained", "what it refers to" in prompt2)
ok("the term is rendered", "THE READING, AS A TERM" in prompt2)
ok("the retype shows in the term", "[as COX enzymes]" in prompt2)

print("=== what was left open is stated as open ===")

s3 = QuerySession("how does aspirin work",
                  Scripted([("aspirin", ASPIRIN), ("infl", INFL)], []),
                  user="marek")
s3.start()
s3.open(ASPIRIN)
s3.skip(Proposal(case=1, retype=COX, options=[]))
s3.resume()
rec3 = record_from_session(s3)
prompt3 = build_prompt(rec3)

ok("the open section is there", "WHAT THEY LEFT OPEN" in prompt3)
ok("the skipped entity is named by LABEL", "COX enzymes" in prompt3)
ok("the unstarted subquery is named", "inflammation" in prompt3)
ok("told not to silently pick a reading",
   "Do not silently pick one reading" in prompt3)

# no open points -> no such section
ok("no open section when nothing was left",
   "WHAT THEY LEFT OPEN" not in prompt2)

print("=== a reflection reads back in the term ===")

p3 = Proposal(case=3, point="aspirin", question="a pair?",
              options=[Option("q/a", entity_a=COX, entity_b=PAIN),
                       Option("other", entity_a=COX, entity_b=INFL)])
s4 = QuerySession("how does aspirin work",
                  Scripted([("aspirin", ASPIRIN)], [p3]), user="marek")
s4.start()
s4.open(ASPIRIN)
prop = s4.propose()
s4.apply(prop, prop.options[0])
s4.close()
prompt4 = build_prompt(record_from_session(s4))
ok("the fan is rendered", "▽" in prompt4)
ok("both casts named", "COX enzymes" in prompt4 and "pain relief" in prompt4)
ok("the pair case is explained", "question-and-answer pair" in prompt4)

# term can be left out
short = build_prompt(rec2, include_term=False)
ok("term omitted on request", "THE READING, AS A TERM" not in short)
ok("clarifications still there", "WHAT THEY CLARIFIED" in short)

# ── the delegate interface ────────────────────────────────────────────────
print("=== the answerer is swappable ===")

echo = EchoAnswerer()
out = echo.answer(rec2)
check("echo returns the prompt", out, echo.last_prompt)


class FakeTransport:
    """Stands in for ClaudeCLITransport / OpenAITransport."""

    model = "fake"
    last_cost_usd = total_cost_usd = 0.0
    last_wall_s = last_api_s = last_duration_s = 0.0

    def __init__(self):
        self.seen_prompt = None
        self.seen_schema = None

    def new_session(self):
        pass

    def invoke(self, prompt, schema):
        self.seen_prompt, self.seen_schema = prompt, schema
        return {"answer": "Aspirin blocks COX enzymes."}


tr = FakeTransport()
ta = TransportAnswerer(tr)
got = ta.answer(rec2)
check("answer comes back as prose", got, "Aspirin blocks COX enzymes.")
ok("the prompt reached the transport", "how does aspirin work" in tr.seen_prompt)
check("asked against a one-field schema",
      sorted(tr.seen_schema["properties"]), ["answer"])
ok("the clarifications reached it too", "by blocking COX" in tr.seen_prompt)

# a transport that answers with nothing
class Empty(FakeTransport):
    def invoke(self, prompt, schema):
        return {}


check("empty answer is empty, not a crash",
      TransportAnswerer(Empty()).answer(rec2), "")

# ── the step ──────────────────────────────────────────────────────────────
print("=== answer_query records the answer beside the interaction ===")

store = InteractionStore()
rid = store.save(rec2)
res = answer_query(store.get(rid), TransportAnswerer(FakeTransport()), store=store)

ok("returns an Answered", isinstance(res, Answered))
check("the answer", res.answer, "Aspirin blocks COX enzymes.")
ok("the prompt is reported back", "how does aspirin work" in res.prompt)
check("saved with the record", store.get(rid).answer,
      "Aspirin blocks COX enzymes.")
check("the interaction is still there", len(store.get(rid).interactions), 1)
check("the query is still there", store.get(rid).query, "how does aspirin work")

# default answerer needs nothing
res2 = answer_query(rec2)
ok("defaults to echo", "WHAT THEY CLARIFIED" in res2.answer)

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
