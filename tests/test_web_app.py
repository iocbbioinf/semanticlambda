"""Tests for the web layer — the whole loop, driven over HTTP.

Run from the project root:  .venv/bin/python tests/test_web_app.py

No model, no database, no network: a scripted StepSource, the in-memory store
and EchoAnswerer. The point is that a user can get from a name to an answer
through real requests.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from bot_guard import Guard, RateLimit, Turnstile, Verdict
from interaction_engine import Entity, Option, Proposal
from interaction_store import InteractionStore
from web_app import create_app
from web_sessions import SessionRegistry

FAILED: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  {name:<48} OK")
    else:
        print(f"  {name:<48} FAIL  got={got!r} want={want!r}")
        FAILED.append(name)


def ok(name, cond):
    check(name, bool(cond), True)


ASP = Entity("aspirin", "aspirin")
COX = Entity("cox", "COX enzymes")
PAIN = Entity("pain", "pain relief")


class Scripted:
    def __init__(self, proposals, seeds=None):
        self._seeds = seeds or [("aspirin", ASP)]
        self._proposals = list(proposals)

    def seeds(self, query):
        return list(self._seeds)

    def propose(self, *a, **k):
        return self._proposals.pop(0) if self._proposals else Proposal(case=0)


P1 = Proposal(case=1, point="how aspirin works", question="In what sense?",
              retype=COX,
              options=[Option("by blocking COX", entity=COX, rationale="the enzyme"),
                       Option("by easing pain", entity=PAIN)])


def client(proposals=(), store=None, guard=None, seeds=None):
    app = create_app(source=Scripted(proposals, seeds),
                     store=store or InteractionStore(),
                     guard=guard, registry=SessionRegistry())
    return TestClient(app), app


# ── the walk from name to answer ──────────────────────────────────────────
print("=== a user gets from a name to an answer ===")

c, app = client([P1])
r = c.get("/")
check("name form", r.status_code, 200)
ok("asks for a name", "Your name" in r.text)

r = c.post("/start", data={"name": "marek"})
check("start redirects", r.status_code, 200)   # followed
ok("cookie set", "sl_session" in c.cookies)
ok("now at the query form", "What do you want to know" in r.text)
ok("greets by name", "marek" in r.text)

r = c.post("/query", data={"query": "how does aspirin work"})
ok("reaches a step", "In what sense?" in r.text)
ok("the point is shown", "how aspirin works" in r.text)
ok("both options offered", "by blocking COX" in r.text and "by easing pain" in r.text)
ok("the rationale shows", "the enzyme" in r.text)
ok("skip is offered", "Skip this point" in r.text)

r = c.post("/choose", data={"choice": "0"})
ok("choosing reaches the answer", "The answer" in r.text)
ok("the query is shown back", "how does aspirin work" in r.text)
ok("how it was read is shown", "How it was read" in r.text)
ok("the chosen answer is recorded", "by blocking COX" in r.text)

# the record landed in the store, with the user on it
recs = app.state.store.for_user("marek")
check("one record saved", len(recs), 1)
check("query saved", recs[0].query, "how does aspirin work")
check("user saved", recs[0].user, "marek")
check("interaction saved", len(recs[0].interactions), 1)
ok("answer saved", bool(recs[0].answer))

r = c.get("/history")
ok("history lists it", "how does aspirin work" in r.text)

# ── no session ────────────────────────────────────────────────────────────
print("=== without a session you are sent back ===")

c2, _ = client([P1])
r = c2.get("/query", follow_redirects=False)
check("query form redirects", r.status_code, 303)
check("...to the name form", r.headers["location"], "/")
r = c2.get("/step", follow_redirects=False)
check("step redirects", r.status_code, 303)
r = c2.post("/choose", data={"choice": "0"}, follow_redirects=False)
check("choose redirects", r.status_code, 303)
r = c2.post("/start", data={"name": "   "}, follow_redirects=False)
check("a blank name is refused", r.headers["location"], "/")

# ── skip and resume ───────────────────────────────────────────────────────
print("=== skip (case 4) and resume (case 5) ===")

c3, app3 = client([P1, P1])
c3.post("/start", data={"name": "u"})
c3.post("/query", data={"query": "q"})
r = c3.post("/skip")
ok("skipping moves on", r.status_code == 200)

print("=== /step is idempotent: a reload must not spend a step ===")


class Counting(Scripted):
    """Counts how often the delegate was asked."""

    def __init__(self, proposals, seeds=None):
        super().__init__(proposals, seeds)
        self.calls = 0

    def propose(self, *a, **k):
        self.calls += 1
        return super().propose(*a, **k)


app_c = create_app(source=Counting([P1, P1, P1]), store=InteractionStore(),
                   registry=SessionRegistry())
cc = TestClient(app_c)
cc.post("/start", data={"name": "u"})
cc.post("/query", data={"query": "q"})
first = cc.get("/step").text
before = app_c.state.source.calls
again = cc.get("/step").text
check("reloading asks the delegate again", app_c.state.source.calls, before)
ok("...and shows the same question", first == again)
# the question must not drift between showing and answering
ok("the question is stable", "In what sense?" in again)

c4, app4 = client([P1])
c4.post("/start", data={"name": "u"})
c4.post("/query", data={"query": "q"})
r = c4.post("/resume")
ok("resume answers straight away", "The answer" in r.text)
rec = app4.state.store.for_user("u")[0]
ok("what was left open is recorded", len(rec.lambda_list) >= 1)
ok("and shown", "Left open" in r.text)

# RESUME ENDS THE WHOLE QUERY. It must not leave another subquery to be
# opened, or the user answers one question and is immediately handed the next.
c4b, app4b = client([P1], seeds=[("aspirin", ASP), ("pain", PAIN),
                                 ("cox", COX)])
c4b.post("/start", data={"name": "u"})
c4b.post("/query", data={"query": "q"})
c4b.post("/resume")
r = c4b.get("/step", follow_redirects=False)
check("after resume, /step goes to the result", r.headers["location"], "/result")
r = c4b.get("/query")
ok("and a new query does not auto-start", "What do you want to know" in r.text)
rec = app4b.state.store.for_user("u")[0]
ok("every unread subquery was bound instead",
   {"aspirin", "pain", "cox"} <= set(rec.lambda_list))

# ── several subqueries ────────────────────────────────────────────────────
print("=== several subqueries are read in turn ===")

c5, app5 = client([P1], seeds=[("aspirin", ASP), ("pain", PAIN)])
c5.post("/start", data={"name": "u"})
c5.post("/query", data={"query": "q"})
r = c5.post("/choose", data={"choice": "0"})
# the first subquery had one point, the second none -> both close, answer shown
ok("reaches the answer after both", "The answer" in r.text)
rec = app5.state.store.for_user("u")[0]
check("both subqueries closed", len(rec.interactions), 2)

# ── the guard ─────────────────────────────────────────────────────────────
print("=== bot protection sits on query submit ===")

# rate limit: the third query is refused
guard = Guard(turnstile=Turnstile(), rate=RateLimit(limit=2, window=3600))
c6, app6 = client([], guard=guard)
c6.post("/start", data={"name": "u"})
r1 = c6.post("/query", data={"query": "one"})
r2 = c6.post("/query", data={"query": "two"})
r3 = c6.post("/query", data={"query": "three"})
ok("the third is refused", "too many queries" in r3.text)
ok("the first was not", "too many queries" not in r1.text)

# turnstile off when unconfigured
ok("unconfigured turnstile is off", not Turnstile().enabled)
check("...and passes", Turnstile().verify("").ok, True)

# configured turnstile with no token is refused
ts = Turnstile(secret="s", site_key="k")
ok("configured turnstile is on", ts.enabled)
check("no token is refused", ts.verify("").ok, False)

# a configured turnstile renders its widget
c7, _ = client([], guard=Guard(turnstile=ts, rate=RateLimit()))
c7.post("/start", data={"name": "u"})
r = c7.get("/query")
ok("the widget is rendered", "cf-turnstile" in r.text)
ok("the site key is in the page", 'data-sitekey="k"' in r.text)

# and a failing verify blocks the query
class Failing(Turnstile):
    def verify(self, token, remote_ip=""):
        return Verdict(False, "challenge failed — try again")


c8, app8 = client([P1], guard=Guard(turnstile=Failing("s", "k"), rate=RateLimit()))
c8.post("/start", data={"name": "u"})
r = c8.post("/query", data={"query": "q"})
ok("a failed challenge blocks the query", "challenge failed" in r.text)
check("and nothing was spent", len(app8.state.store.all()), 0)

# ── sessions ──────────────────────────────────────────────────────────────
print("=== sessions ===")

reg = SessionRegistry()
s = reg.new("marek")
ok("found by id", reg.get(s.sid) is not None)
check("unknown id", reg.get("nope"), None)
check("no id", reg.get(None), None)
check("one live", len(reg), 1)
reg.drop(s.sid)
check("dropped", len(reg), 0)

expired = SessionRegistry(ttl=-1)
s2 = expired.new("u")
check("expired sessions are not returned", expired.get(s2.sid), None)

reg2 = SessionRegistry(ttl=-1)
reg2.new("a")
reg2.new("b")
check("sweep drops them", reg2.sweep(), 2)

# two names are two sessions
reg3 = SessionRegistry()
a, b = reg3.new("same"), reg3.new("same")
ok("same name, different sessions", a.sid != b.sid)

print("=== verbose mode shows the calculus ===")

app_v = create_app(source=Scripted([P1, P1]), store=InteractionStore(),
                   registry=SessionRegistry(), verbose=True,
                   guard=Guard(turnstile=Turnstile(), rate=RateLimit(limit=99)))
cv = TestClient(app_v)
cv.post("/start", data={"name": "v"})
cv.post("/query", data={"query": "how does aspirin work"})
t = cv.get("/step").text

ok("the panel is there", "The calculus" in t)
ok("1. subqueries are listed", "subqueries" in t and "aspirin" in t)
ok("   ...and which is being read", "← reading" in t)
ok("2. the unclear point and its entity", "unclear point" in t)
ok("   ...mapped to the retype", "COX enzymes" in t)
ok("3. which case was chosen", "case 1 — contraction opt.1" in t)
ok("   ...with the options and their entities", "options" in t)
ok("4. the term is rendered", 'class="term"' in t)
ok("   ...with the pointer set", "P = {p0@root}" in t)
# pointer 0 must not be blanked by a falsy-zero bug
ok("   ...and actPtr 0 is shown, not blank", "act = 0" in t)

r = cv.post("/choose", data={"choice": "0"})
t2 = cv.get("/step").text
ok("the term grows with the step", "[as COX enzymes]" in t2)

# 5. the interaction question, on the result page
cv.post("/resume")
t3 = cv.get("/step", follow_redirects=True).text
ok("5. what was saved is shown", "What was saved" in t3)
ok("   ...including the λ-question", "λ-question" in t3)
ok("   ...and the λ-list", "λ-list" in t3)
ok("   ...and the record id", "saved as" in t3)

# verbose is OFF by default
c_off, _ = client([P1])
c_off.post("/start", data={"name": "u"})
c_off.post("/query", data={"query": "q"})
ok("off by default", "The calculus" not in c_off.get("/step").text)

print("=== a delegate failure is shown, not a 500 ===")


class Broken:
    """A delegate that cannot be reached — a bad key, a dead endpoint."""

    def seeds(self, query):
        raise RuntimeError("the OpenAI API call failed: Error code: 401")

    def propose(self, *a, **k):
        return Proposal(case=0)


app_b = create_app(source=Broken(), store=InteractionStore(),
                   registry=SessionRegistry())
cb = TestClient(app_b)
cb.post("/start", data={"name": "u"})
r = cb.post("/query", data={"query": "q"}, follow_redirects=False)
check("it redirects rather than crashing", r.status_code, 303)
ok("...to the query form", r.headers["location"].startswith("/query?err="))
# the message must survive the round trip readably
loc = r.headers["location"]
page_text = cb.get(loc).text
ok("the provider's own words reach the user", "401" in page_text)
ok("...as an error", 'class="err"' in page_text)
check("and nothing was saved", len(app_b.state.store.all()), 0)

print("=== health ===")
c9, _ = client([])
r = c9.get("/healthz")
check("healthz", r.json()["ok"], True)

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
