"""Tests for interaction_state.py — the rewritten term/typing/pointer layer.

Run from the project root:  .venv/bin/python tests/test_interaction_state.py

Covers the spec's five cases and the typing that distinguishes them:
  * the RETYPE is local to one application (the point Marek settled)
  * [app(ta,tb)] = [tb], the rightmost leaf
  * case 1 / case 2 / case 3 / skip / resume on the pointer set
  * sharing survives a save/load round-trip
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from optimal_lambda import LamVar
from interaction_state import (
    HOLE, EntityRegistry, LamApp, LamFan, PointerSet,
    func_type_at, lam_from_dict_shared, lam_to_dict_shared,
    replace_at, subterm_at, type_in_context, type_of,
)

FAILED: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  {name:<44} OK")
    else:
        print(f"  {name:<44} FAIL  got={got!r} want={want!r}")
        FAILED.append(name)


def ok(name, cond):
    check(name, bool(cond), True)


# ── the retype ────────────────────────────────────────────────────────────
print("=== retype: [ta] in app(ta,tb) is C, elsewhere it is A ===")

reg = EntityRegistry()
a = reg.get("A", "A")
b = reg.get("B", "B")
z = reg.get("Z", "Z")

# app(ta, tb) with ta retyped to C at THIS application
app1 = LamApp(func=a, arg=b, func_type="C")
# the SAME ta used as the function of another application, not retyped
app2 = LamApp(func=a, arg=z)

check("[ta] alone", type_of(a), "A")
check("[ta] in app1 (retyped)", type_in_context(app1, (0,)), "C")
check("[ta] in app2 (not retyped)", type_in_context(app2, (0,)), "A")
ok("one node, two applications", app1.func is app2.func)
check("retype recorded on app1", func_type_at(app1, ()), "C")
check("app2 carries no retype", func_type_at(app2, ()), None)

# a second retype of the same node, at a third application, is independent
app3 = LamApp(func=a, arg=z, func_type="E")
check("[ta] in app3", type_in_context(app3, (0,)), "E")
check("app1 unaffected by app3", type_in_context(app1, (0,)), "C")
check("[ta] still A on its own", type_of(a), "A")

print("=== result typing: [app(ta,tb)] = [tb], rightmost leaf ===")
check("[app(a,b)]", type_of(app1), "B")
check("[app(a,z)]", type_of(app2), "Z")
# case 1: app(ta, td) with [td]=D  ->  [app]=D
d = reg.get("D", "D")
check("case 1 [app(ta,td)] = D", type_of(LamApp(func=a, arg=d, func_type="C")), "D")
# case 2: app(a, tb) with [tb]=B  ->  [app]=B
check("case 2 [app(a,tb)] = B", type_of(LamApp(func=a, arg=b)), "B")
# rightmost leaf of a right-nested spine
spine = LamApp(func=a, arg=LamApp(func=b, arg=d))
check("rightmost leaf of a spine", type_of(spine), "D")

# ── case 1 ────────────────────────────────────────────────────────────────
print("=== case 1: contraction option 1 ===")

ps = PointerSet.initial()
act = ps.act()
check("opens with one pointer", len(ps), 1)
check("at the root", act.path, ())

# term becomes app(ta, td); the stayed-at term is the FUNCTION
t = replace_at(a, (), LamApp(func=a, arg=d, func_type="C"))
ps.after_contraction(act, option=1)
check("actPtr keeps its path", ps.act().path, ())
check("|P| unchanged", len(ps), 1)
check("[t] = D (moved to the answer)", type_of(t), "D")
check("[ta] in place = C", type_in_context(t, (0,)), "C")

# a pointer below the contracted position is rebased under branch 0
ps2 = PointerSet.initial()
deep = ps2._mint((0,), "init")
ps2.after_contraction(ps2.act(), option=1)
check("case 1 rebases under branch 0", deep.path, (0, 0))

# ── case 2 ────────────────────────────────────────────────────────────────
print("=== case 2: contraction option 2 ===")

ps3 = PointerSet.initial()
t2 = LamApp(func=a, arg=b)          # app(a, tb): the stayed-at term is the ARG
ps3.after_contraction(ps3.act(), option=2)
check("[app(a,tb)] = B (user stays)", type_of(t2), "B")
check("actPtr keeps its path", ps3.act().path, ())

ps4 = PointerSet.initial()
deep4 = ps4._mint((1,), "init")
ps4.after_contraction(ps4.act(), option=2)
check("case 2 rebases under branch 1", deep4.path, (1, 1))

# ── case 3 ────────────────────────────────────────────────────────────────
print("=== case 3: reflection ===")

c = reg.get("C", "C")
fan = LamFan(principal=c, grey_cast="A", black_cast="B")
ps5 = PointerSet.initial()
left, right = ps5.after_reflection(ps5.act(), "A", "B")

check("|P| grows by one", len(ps5), 2)
check("left occurrence path", left.path, (0,))
check("right occurrence path", right.path, (1,))
check("question cast on the left", left.cast_type, "A")
check("answer cast on the right", right.cast_type, "B")
check("paired by reflect_id", left.reflect_id, right.reflect_id)
ok("old pointer is gone", ps5.get(0) is None)

# both occurrences are ONE node and both type as C
ok("one shared subject", subterm_at(fan, (0,)) is subterm_at(fan, (1,)))
check("both [tc] = C (left)", type_of(subterm_at(fan, (0,))), "C")
check("both [tc] = C (right)", type_of(subterm_at(fan, (1,))), "C")

# contracting at ONE occurrence wraps only that one; the subject stays shared
wrapped = replace_at(fan, (0,), LamApp(func=a, arg=fan.principal))
ok("grey wrapped", isinstance(wrapped.grey, LamApp))
ok("black untouched", wrapped.black is c)
ok("subject still one node", wrapped.principal is c)

# ── case 4 / case 5 ───────────────────────────────────────────────────────
print("=== case 4 skip, case 5 resume ===")

ps6 = PointerSet.initial()
second = ps6._mint((1,), "contract")
ps6.skip(ps6.act())
check("skip keeps the pointer in P", len(ps6), 2)
check("skipped is not offered", [p.pid for p in ps6.open_pointers()], [second.pid])
check("actPtr moves on", ps6.act_pid, second.pid)
ok("re-selecting un-skips", ps6.select(0) and not ps6.get(0).skipped)

ps7 = PointerSet.initial()
ps7._mint((1,), "contract")
leftover = ps7.resume()
check("resume returns every open point", len(leftover), 2)
check("nothing left open", ps7.open_pointers(), [])
check("resume settles nothing (P intact)", len(ps7), 2)

# ── round-trip ────────────────────────────────────────────────────────────
print("=== save/load: sharing and retypes survive ===")

reg2 = EntityRegistry()
x = reg2.get("X", "X")
shared = LamFan(principal=x, grey_cast="A", black_cast="B")
term = LamApp(func=shared, arg=reg2.get("Y", "Y"), func_type="C")

back = lam_from_dict_shared(lam_to_dict_shared(term))
check("retype survives", back.func_type, "C")
check("[t] survives", type_of(back), "Y")
ok("fan survives", isinstance(back.func, LamFan))
ok("subject is ONE node after load",
   subterm_at(back.func, (0,)) is subterm_at(back.func, (1,)))
check("casts survive", (back.func.grey_cast, back.func.black_cast), ("A", "B"))

# entity reuse is rebuilt as one node through a single registry
two_uses = LamApp(func=reg2.get("X", "X"), arg=reg2.get("X", "X"))
back2 = lam_from_dict_shared(lam_to_dict_shared(two_uses))
ok("reused entity reloads as one node", back2.func is back2.arg)

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
