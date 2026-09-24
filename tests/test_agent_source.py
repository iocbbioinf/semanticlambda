"""AgentSource is a BOUNDARY: what a delegate sends becomes engine types here.

The schema a delegate fills requires only `label` on an option, and json-mode
providers do not enforce even that much — so an option can arrive naming no
entity at all. The engine cannot use one: `_operand` has an entity to contribute
or it has nothing. These tests hold the boundary to dropping what the case
cannot use, so a bad option never reaches the term builder.

Nothing here calls a network or spawns a process.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mock_source import AgentSource

FAILED = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILED.append(msg)


class ScriptedTransport:
    """Answers with one canned dict, whatever it is asked."""

    def __init__(self, reply):
        self.reply = reply

    def invoke(self, prompt, schema):
        return self.reply


def propose(reply):
    src = AgentSource(ScriptedTransport(reply))
    from interaction_engine import Entity
    return src.propose("q", "part", None, Entity("here", "here"), [])


print("=== cases 1 and 2 need an entity ===")

p = propose({"case": 1, "question": "which?", "options": [
    {"label": "names an entity", "entity": "binding affinity"},
    {"label": "names none"},
    {"label": "names a blank one", "entity": "   "},
]})
check(p.case == 1, "case survives")
check([o.label for o in p.options] == ["names an entity"],
      "options with no entity are dropped")

p = propose({"case": 2, "options": [
    {"label": "no entity"}, {"label": "also none", "entity": ""},
]})
check(p.options == [], "a case 2 option with no entity is dropped too")

print("=== case 3 needs BOTH sides ===")

p = propose({"case": 3, "question": "split?", "options": [
    {"label": "both sides", "entity_a": "the cause", "entity_b": "the effect"},
    {"label": "only one side", "entity_a": "the cause"},
    {"label": "the wrong field", "entity": "the cause"},
]})
check([o.label for o in p.options] == ["both sides"],
      "only the option with both sides survives")
check(p.options[0].entity_a is not None and p.options[0].entity_b is not None,
      "and it carries them")

print("=== a misplaced field is recovered, a missing one is not ===")

# The delegate picked the case correctly and then filled the neighbouring
# field. Recovering that is the difference between case 2/3 working and the
# step degrading to case 0, which reads as "it only ever picks case 1".
p = propose({"case": 2, "options": [
    {"label": "as a drug target", "entity_a": "drug target"},
    {"label": "as a counter-screen", "entity_a": "selectivity screen"},
]})
check(p.case == 2, "case 2 survives a misplaced entity")
check([o.entity.label for o in p.options if o.entity]
      == ["drug target", "selectivity screen"],
      "entity_a is read as entity for case 2")

p = propose({"case": 3, "options": [
    {"label": "criterion and evidence", "entity": "reaches the brain",
     "entity_b": "measured BBB permeability"},
]})
check(p.case == 3 and len(p.options) == 1, "case 3 survives a misplaced side")
check(p.options[0].entity_a.label == "reaches the brain"
      and p.options[0].entity_b.label == "measured BBB permeability",
      "entity fills the empty side, in the right order")

# But a side that was never sent is NOT invented. The option is dropped, and
# the empty proposal becomes case 0 one layer up, in `QuerySession.propose`.
p = propose({"case": 3, "options": [
    {"label": "only a question side", "entity_a": "reaches the brain"},
]})
check(p.options == [], "case 3 with one side only is still dropped")

p = propose({"case": 1, "options": [
    {"label": "both sides sent", "entity_a": "a", "entity_b": "b"},
    {"label": "plain", "entity": "c"},
]})
check([o.label for o in p.options] == ["plain"],
      "a full pair is not squeezed into case 1")

print("=== an option still needs a label ===")

p = propose({"case": 1, "options": [
    {"label": "", "entity": "a"}, {"entity": "b"},
    {"label": "kept", "entity": "c"},
]})
check([o.label for o in p.options] == ["kept"], "unlabelled options are dropped")

print("=== case 0 is passed through ===")

check(propose({"case": 0}).case == 0, "nothing unclear stays nothing unclear")
check(propose({}).case == 0, "a reply with no case at all is case 0")

print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED"))
sys.exit(1 if FAILED else 0)
