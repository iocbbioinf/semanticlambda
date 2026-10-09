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


print("=== case 1 needs an entity ===")

p = propose({"case": 1, "question": "which?", "options": [
    {"label": "names an entity", "entity": "binding affinity"},
    {"label": "names none"},
    {"label": "names a blank one", "entity": "   "},
]})
check(p.case == 1, "case survives")
check([o.label for o in p.options] == ["names an entity"],
      "options with no entity are dropped")

print("=== cases 2 and 3 are not in the loop ===")

for c in (2, 3):
    check(propose({"case": c, "options": [
        {"label": "x", "entity": "a"}, {"label": "y", "entity": "b"},
    ]}).case == 0, f"case {c} becomes case 0")

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
