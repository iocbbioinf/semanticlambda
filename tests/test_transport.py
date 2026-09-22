"""The delegate is PLUGGABLE: who answers is separable from what is asked.

`ReadingAgent` owns the prompts, the schemas and the decoding; a TRANSPORT owns
getting one schema-conforming dict back. What these tests hold to is that the
seam is real — the agent works over any transport, the counters every display
reads still reach it, and the OpenAI transport's strict-schema rewrite does not
lie about which fields the calculus requires.

Nothing here calls a network or spawns a process.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reading_agent as ra
from reading_agent import ReadingAgent
from reading_transport import (AgentError, ClaudeCLITransport, OpenAITransport,
                               Transport, _Counters)

FAILED = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILED.append(msg)


class FakeTransport(_Counters):
    """A transport that answers from a script — no process, no network."""

    def __init__(self, replies=None):
        super().__init__("fake-model")
        self.replies = replies or {}
        self.prompts = []
        self.schemas = []
        self.resets = 0

    def new_session(self) -> None:
        self.resets += 1

    def invoke(self, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        self._record(wall_s=0.5, cost=0.001, api_s=0.4, duration_s=0.45)
        if "entities" in (schema.get("required") or []):
            return self.replies.get("seed", {
                "entities": [
                    {"id": "aspirin", "label": "Aspirin", "gloss": "a drug"},
                    {"id": "local:cox", "label": "COX"},
                ]})
        return self.replies.get("step", {
            "kind": "A", "prompt": "which?",
            "options": [
                {"label": "as a process", "rationale": "r1",
                 "entity": {"id": "proc", "label": "process"}},
                {"label": "as a structure", "rationale": "r2",
                 "entity": {"id": "struct", "label": "structure"}},
            ]})


# ── the seam ──────────────────────────────────────────────────────────────────


def test_the_agent_works_over_any_transport():
    """Neither prompts nor decoding know who answers."""
    t = FakeTransport()
    agent = ReadingAgent(transport=t)

    seeds = agent.seed_entities("how does aspirin work?")
    check([e.iri for e in seeds] == ["local:aspirin", "local:cox"],
          "seed entities decode from a fake transport's reply")

    step = agent.propose_step(query="q", term_text="x", here=seeds[0],
                              history=[], reached_from=None, known=seeds,
                              allow_c=True)
    check(step.kind == "A" and len(step.options) == 2,
          "a step proposal decodes the same way (A, 2 options)")
    check(len(t.prompts) == 2 and "aspirin" in t.prompts[0],
          "the agent's own prompts reached the transport unchanged")


def test_a_reused_entity_is_still_one_node():
    """The sharing invariant is the DECODER's, so a transport swap cannot break
    it: an id already in the reading comes back as the SAME object (§1)."""
    t = FakeTransport(replies={"step": {
        "kind": "A", "prompt": "which?",
        "options": [
            {"label": "a new sense", "entity": {"id": "proc", "label": "process"}},
            {"label": "back to aspirin",
             "entity": {"id": "local:aspirin", "label": "Aspirin"}},
        ]}})
    agent = ReadingAgent(transport=t)
    seeds = agent.seed_entities("q")
    step = agent.propose_step(query="q", term_text="x", here=seeds[0],
                              history=[], reached_from=None, known=seeds,
                              allow_c=False)
    check(step.options[1].entity is seeds[0],
          "an entity named again is the same object, not a twin")


def test_the_counters_every_display_reads_reach_the_transport():
    """`--verbose` /cost and /time read these off the AGENT, as they did when it
    was the transport. They must not silently become zero."""
    t = FakeTransport()
    agent = ReadingAgent(transport=t)
    agent.seed_entities("q")
    check(agent.last_cost_usd == 0.001 and agent.total_cost_usd == 0.001,
          "cost reaches the agent (last and total)")
    agent.seed_entities("q")
    check(agent.total_cost_usd == 0.002 and agent.calls == 2,
          "and accumulates over calls")
    check(agent.last_wall_s == 0.5 and agent.last_api_s == 0.4
          and agent.last_duration_s == 0.45 and agent.total_wall_s == 1.0,
          "the timing the /time line shows reaches it too")
    check(agent.model == "fake-model", "so does the model name")


def test_new_session_reaches_the_transport():
    """ONE SESSION PER QUERY is the transport's to honour, whoever it is."""
    t = FakeTransport()
    agent = ReadingAgent(transport=t)
    agent.new_session()
    check(t.resets == 1, "the agent's new_session() reaches the transport")


def test_clarify_plans_direct_invoke_still_works():
    """`clarify_plan` writes its own prompts and schemas but wants the same
    delegate and the same accounting, so it calls `_invoke` directly."""
    t = FakeTransport(replies={"step": {"kind": "none", "options": []}})
    agent = ReadingAgent(transport=t)
    out = agent._invoke("a planner prompt", {"type": "object"})
    check(out == {"kind": "none", "options": []},
          "_invoke passes through to the transport")
    check(agent.total_cost_usd == 0.001,
          "and the planner's cost accounting still sees the charge")


def test_the_default_is_still_the_cli():
    """A swap must not change what `ReadingAgent(...)` has always meant."""
    agent = ReadingAgent()
    check(isinstance(agent.transport, ClaudeCLITransport),
          "with no transport given, the delegate is the Claude CLI")
    check(agent.model == "sonnet", "with the model it always defaulted to")
    check(ReadingAgent(model="opus").model == "opus",
          "and an explicit model still reaches it")


def test_both_transports_satisfy_the_protocol():
    check(isinstance(ClaudeCLITransport(), Transport),
          "ClaudeCLITransport is a Transport")
    check(isinstance(FakeTransport(), Transport),
          "so is anything with invoke/new_session and the counters")


# ── the OpenAI transport's schema rewrite ─────────────────────────────────────


def _walk_strict(node, path="root"):
    """Every object must be closed and require all its properties."""
    bad = []
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            if node.get("additionalProperties") is not False:
                bad.append(f"{path}: not closed")
            if set(node.get("required") or []) != set(node["properties"]):
                bad.append(f"{path}: required != properties")
        for k, v in node.items():
            bad += _walk_strict(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            bad += _walk_strict(v, f"{path}[{i}]")
    return bad


def test_strict_rewrite_satisfies_structured_outputs():
    for name, schema in (("step", ra._STEP_SCHEMA), ("seed", ra._SEED_SCHEMA)):
        bad = _walk_strict(OpenAITransport._strict(schema))
        check(not bad, f"the {name} schema rewrites to a strict one ({bad})")


def test_strict_rewrite_keeps_what_was_required_required():
    """Widening an OPTIONAL field to nullable is honest; widening a REQUIRED one
    would let the delegate omit an id the calculus needs."""
    item = (OpenAITransport._strict(ra._SEED_SCHEMA)
            ["properties"]["entities"]["items"])
    check(item["properties"]["id"]["type"] == "string",
          "a required id stays non-nullable")
    check(item["properties"]["label"]["type"] == "string",
          "so does a required label")
    check(item["properties"]["gloss"]["type"] == ["string", "null"],
          "an optional gloss widens to nullable instead of becoming mandatory")


def test_strict_rewrite_does_not_mutate_the_shared_schema():
    """The CLI transport uses the same dicts, and `_ENTITY_PROPS` is shared by
    both schemas — a rewrite in place would corrupt every later call."""
    before = json.dumps(ra._STEP_SCHEMA, sort_keys=True)
    OpenAITransport._strict(ra._STEP_SCHEMA)
    OpenAITransport._strict(ra._SEED_SCHEMA)
    check(json.dumps(ra._STEP_SCHEMA, sort_keys=True) == before,
          "the original schema is untouched by the rewrite")
    check(ra._ENTITY_PROPS["gloss"]["type"] == "string",
          "and the shared entity props are not widened in place")


def test_a_nulled_optional_decodes_as_absent():
    """The rewrite's whole premise: the decoders already read null as missing,
    so an A-kind option carrying `entity_a: null` must still decode."""
    t = FakeTransport(replies={"step": {
        "kind": "A", "prompt": "which?", "note": None,
        "options": [
            {"label": "one", "rationale": None, "entity_a": None,
             "entity_b": None, "entity": {"id": "a", "label": "A",
                                          "gloss": None}},
            {"label": "two", "rationale": None, "entity_a": None,
             "entity_b": None, "entity": {"id": "b", "label": "B",
                                          "gloss": None}},
        ]}})
    agent = ReadingAgent(transport=t)
    step = agent.propose_step(query="q", term_text="x",
                              here=ra.Entity("local:x", "X"), history=[],
                              reached_from=None, known=[], allow_c=False)
    check(step.kind == "A" and len(step.options) == 2,
          "nulled optionals decode as absent, not as an error")
    check(step.options[0].entity.gloss == "" and step.options[0].rationale == "",
          "a null gloss/rationale reads as empty")


def test_openai_transport_refuses_without_a_key():
    """A setup mistake must be an AgentError the browser can print, not a
    traceback — and never a call made with no credentials."""
    import os
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        try:
            OpenAITransport()
            check(False, "constructing without a key should raise")
        except AgentError as e:
            msg = str(e)
            check("OPENAI_API_KEY" in msg or "openai" in msg,
                  f"it raises AgentError explaining the setup ({msg[:60]})")
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved


def test_loose_json_parsing():
    """JSON mode on a smaller model does not always return bare JSON.

    A fence, a reasoning preamble or a sentence before the brace are all
    recoverable, and recovering them is the difference between an interaction
    step working and the whole query failing.
    """
    from reading_transport import _loads_loose

    check(_loads_loose('{"a": 1}') == {"a": 1}, "bare JSON")
    check(_loads_loose('```json\n{"a": 1}\n```') == {"a": 1}, "a ```json fence")
    check(_loads_loose('```\n{"a": 1}\n```') == {"a": 1}, "a bare fence")
    check(_loads_loose('<think>hmm</think>\n{"a": 1}') == {"a": 1},
          "a reasoning preamble (deepseek-r1)")
    check(_loads_loose('Here you go:\n{"a": 1}\nhope that helps') == {"a": 1},
          "prose either side")
    check(_loads_loose('{"a": {"b": 2}} trailing') == {"a": {"b": 2}},
          "nested braces are balanced, not greedy")
    check(_loads_loose("no json here") is None, "no JSON at all is None")
    check(_loads_loose("") is None, "empty is None")


def test_base_url_selects_json_mode():
    """A third-party endpoint defaults to JSON mode.

    Strict `json_schema` is an OpenAI extension; most compatible providers —
    e-INFRA's LiteLLM/Ollama among them — do not implement it. Defaulting the
    other way would fail every call on a provider that cannot do it.
    """
    import os
    saved = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "test-key"
    try:
        try:
            plain = OpenAITransport(model="gpt-4o")
            check(plain.json_mode is False, "OpenAI itself uses strict schemas")
            check(plain.base_url is None, "...and no base_url")

            other = OpenAITransport(model="llama3.3:latest",
                                    base_url=OpenAITransport.EINFRA_BASE_URL)
            check(other.json_mode is True, "a --base-url defaults to json mode")
            check("e-infra" in other.base_url, "the base_url is kept")

            forced = OpenAITransport(model="m", base_url="https://x/v1",
                                     json_mode=False)
            check(forced.json_mode is False, "--strict-schema can force it off")
        except AgentError as exc:                     # openai not installed
            check("not installed" in str(exc), f"skipped: {exc}")
    finally:
        if saved is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = saved


def test_einfra_token_is_accepted_as_a_key():
    """E_INFRA_API_TOKEN is what their docs tell you to set."""
    import os
    saved_o = os.environ.pop("OPENAI_API_KEY", None)
    os.environ["E_INFRA_API_TOKEN"] = "einfra-token"
    try:
        try:
            t = OpenAITransport(model="llama3.3:latest",
                                base_url=OpenAITransport.EINFRA_BASE_URL)
            check(t.json_mode is True, "E_INFRA_API_TOKEN is accepted as a key")
        except AgentError as exc:
            check("not installed" in str(exc), f"skipped: {exc}")
    finally:
        os.environ.pop("E_INFRA_API_TOKEN", None)
        if saved_o is not None:
            os.environ["OPENAI_API_KEY"] = saved_o


if __name__ == "__main__":
    for t in (test_the_agent_works_over_any_transport,
              test_a_reused_entity_is_still_one_node,
              test_the_counters_every_display_reads_reach_the_transport,
              test_new_session_reaches_the_transport,
              test_clarify_plans_direct_invoke_still_works,
              test_the_default_is_still_the_cli,
              test_both_transports_satisfy_the_protocol,
              test_strict_rewrite_satisfies_structured_outputs,
              test_strict_rewrite_keeps_what_was_required_required,
              test_strict_rewrite_does_not_mutate_the_shared_schema,
              test_a_nulled_optional_decodes_as_absent,
              test_openai_transport_refuses_without_a_key,
              test_loose_json_parsing,
              test_base_url_selects_json_mode,
              test_einfra_token_is_accepted_as_a_key):
        t()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for m in FAILED:
            print(f"  - {m}")
        sys.exit(1)
    print("all passed")
