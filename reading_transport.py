"""The TRANSPORT — how a prompt and a schema become a JSON object.

`ReadingAgent` owns what to ASK (prompts, schemas) and how to read the answer
back into calculus material (entities, options, step kinds). None of that is
specific to who answers. This module owns the other half: getting one
schema-conforming dict back from some delegate, and reporting what it cost.

    ReadingAgent      WHAT is asked, and what the answer MEANS
    Transport         WHO answers, and what that cost

A transport is anything with `invoke(prompt, schema) -> dict` plus the cost and
timing counters the `--verbose` /cost line reads. Two are provided:

    ClaudeCLITransport   `claude -p --json-schema`, the original delegate
    OpenAITransport      the OpenAI chat-completions API, structured outputs

SESSIONS ARE A TRANSPORT CONCERN, NOT THE CALCULUS'S. `ReadingSession` calls
`new_session()` at the start of every query because the senses settled for one
query are not senses of the next — but HOW a context is kept differs sharply by
provider. The CLI keeps it server-side behind a `--session-id`, so a resumed
call sends no history and the app pays startup once. The OpenAI API keeps no
context at all, so the transport must hold the message list itself and resend
it every call, which is why its cost grows with the length of an interaction
where the CLI's does not. Both honour `new_session()`; only the second pays for
the whole history each time.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from typing import Optional, Protocol, runtime_checkable


class AgentError(RuntimeError):
    """The delegate could not be reached, or replied with something unusable."""


DEFAULT_TIMEOUT = 240


@runtime_checkable
class Transport(Protocol):
    """What `ReadingAgent` needs of whoever answers.

    The counters are read by the browser's /cost and /time lines, so every
    transport carries them even when it has nothing to report (the mock leaves
    them at 0.0 and no cost line is printed).
    """

    model: str
    last_cost_usd: float
    total_cost_usd: float
    last_wall_s: float
    last_api_s: float
    last_duration_s: float
    total_wall_s: float
    calls: int

    def invoke(self, prompt: str, schema: dict) -> dict:
        """Answer `prompt` with a dict conforming to `schema`."""
        ...

    def new_session(self) -> None:
        """Forget the context, so the next call starts empty."""
        ...


class _Counters:
    """The accounting every transport keeps, in one place."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.last_cost_usd = 0.0
        self.total_cost_usd = 0.0
        self.last_wall_s = 0.0
        self.last_api_s = 0.0
        self.last_duration_s = 0.0
        self.total_wall_s = 0.0
        self.calls = 0

    def _record(self, wall_s: float, cost: float,
                api_s: float = 0.0, duration_s: float = 0.0) -> None:
        self.last_wall_s = wall_s
        self.total_wall_s += wall_s
        self.last_api_s = api_s or wall_s
        self.last_duration_s = duration_s or wall_s
        self.last_cost_usd = cost
        self.total_cost_usd += cost
        self.calls += 1


# ── Claude Code CLI ───────────────────────────────────────────────────────────


class ClaudeCLITransport(_Counters):
    """`claude -p` with `--json-schema` — the original delegate.

    ONE SESSION PER QUERY. The first call pins a fresh `--session-id`, every
    later call in the same interaction `--resume`s it, so the query and the
    choices already made are context the delegate still holds, and the
    per-process overhead that dominates a cold `claude -p` is paid once.
    Measured on the 5-HT2C query: a cold call reads ~24k cache tokens and costs
    ~$0.02 before any reasoning; a resumed one reads ~28k and writes 63,
    costing ~$0.006 — the difference IS the startup.
    """

    DEFAULT_MODEL = "sonnet"

    def __init__(self, model: str = DEFAULT_MODEL,
                 timeout: int = DEFAULT_TIMEOUT,
                 cwd: Optional[str] = None,
                 session: bool = True) -> None:
        super().__init__(model)
        self.timeout = timeout
        self.cwd = cwd
        self.use_session = session
        self.session_id: Optional[str] = None

    def new_session(self) -> None:
        self.session_id = None

    def invoke(self, prompt: str, schema: dict) -> dict:
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

        self._record(
            wall_s=time.monotonic() - t0,
            cost=envelope.get("total_cost_usd") or 0.0,
            api_s=(envelope.get("duration_api_ms") or 0) / 1000.0,
            duration_s=(envelope.get("duration_ms") or 0) / 1000.0,
        )
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


# ── OpenAI ────────────────────────────────────────────────────────────────────

# Per-million-token prices, so the /cost line means the same thing whichever
# transport is in force. The API reports tokens, never dollars — unlike the CLI
# envelope, which reports `total_cost_usd` directly. Unknown models cost 0.0
# rather than a wrong number: a missing cost line is honest, a fabricated one
# is not.
OPENAI_PRICES_PER_MTOK = {
    "gpt-4o":          (2.50, 10.00),
    "gpt-4o-mini":     (0.15,  0.60),
    "gpt-4.1":         (2.00,  8.00),
    "gpt-4.1-mini":    (0.40,  1.60),
    "gpt-4.1-nano":    (0.10,  0.40),
}


class OpenAITransport(_Counters):
    """The OpenAI chat-completions API with structured outputs.

    THE CONTEXT IS OURS TO CARRY. The API is stateless, so "one session per
    query" means this object holds the message list and resends it on every
    call; `new_session()` clears it. That makes an interaction's cost grow with
    its length, where the CLI's session keeps it flat — the same reason the
    clarification phase batches is twice as good a reason here.

    Structured outputs require a STRICT schema: every property listed in
    `required` and `additionalProperties: false` throughout. The schemas in
    `reading_agent` are written for the CLI, which is laxer, so `_strict`
    rewrites them on the way out rather than duplicating them per provider.
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, model: str = DEFAULT_MODEL,
                 timeout: int = DEFAULT_TIMEOUT,
                 api_key: Optional[str] = None,
                 session: bool = True) -> None:
        super().__init__(model)
        self.timeout = timeout
        self.use_session = session
        self._messages: list[dict] = []
        try:
            from openai import OpenAI
        except ImportError:
            raise AgentError(
                "the `openai` package is not installed — `pip install openai`, "
                "or run without --openai") from None
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise AgentError(
                "OPENAI_API_KEY is not set — export it, or run without --openai")
        self._client = OpenAI(api_key=key, timeout=timeout)

    def new_session(self) -> None:
        self._messages = []

    # ── schema ────────────────────────────────────────────────────────────

    @staticmethod
    def _strict(schema: dict) -> dict:
        """Rewrite a CLI-shaped schema into one structured outputs accepts.

        Two rules, applied to every object in the tree: `additionalProperties`
        must be false, and `required` must name EVERY property. The second is
        the awkward one — the app's schemas mark `gloss`, `rationale`, `entity`
        and friends optional on purpose, since an option of kind A has no
        `entity_a`. Making them required would be a lie about the data, so
        instead each optional property is widened to allow null, which is what
        the decoders in `reading_agent` already treat as absent (`_entity`
        returns None for anything that is not a dict).
        """
        def walk(node):
            if not isinstance(node, dict):
                return node
            out = dict(node)
            if out.get("type") == "array" and "items" in out:
                out["items"] = walk(out["items"])
                return out
            if out.get("type") != "object" or "properties" not in out:
                return out
            props = {k: walk(v) for k, v in out["properties"].items()}
            required = list(out.get("required") or [])
            for name, prop in props.items():
                if name in required:
                    continue
                # Optional -> nullable-and-required, so the schema stays strict
                # while the decoders keep seeing a missing field as missing.
                t = prop.get("type")
                if isinstance(t, str):
                    prop["type"] = [t, "null"]
                elif isinstance(t, list) and "null" not in t:
                    prop["type"] = t + ["null"]
            out["properties"] = props
            out["required"] = list(props)
            out["additionalProperties"] = False
            return out

        return walk(schema)

    # ── transport ─────────────────────────────────────────────────────────

    def _cost(self, usage) -> float:
        prices = OPENAI_PRICES_PER_MTOK.get(self.model)
        if prices is None or usage is None:
            return 0.0
        in_rate, out_rate = prices
        return (getattr(usage, "prompt_tokens", 0) / 1_000_000 * in_rate
                + getattr(usage, "completion_tokens", 0) / 1_000_000 * out_rate)

    def invoke(self, prompt: str, schema: dict) -> dict:
        messages = (self._messages if self.use_session else []) + [
            {"role": "user", "content": prompt}]
        t0 = time.monotonic()
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "reading_step",
                                    "strict": True,
                                    "schema": self._strict(schema)},
                },
            )
        except Exception as exc:                       # the SDK's own errors
            raise AgentError(f"the OpenAI API call failed: "
                             f"{str(exc)[:400]}") from None

        text = (resp.choices[0].message.content or "").strip()
        self._record(wall_s=time.monotonic() - t0,
                     cost=self._cost(getattr(resp, "usage", None)))
        if self.use_session:
            # Keep the exchange, so the next call of this query has the context
            # the CLI would have kept server-side. This is what we pay for again
            # on every later call.
            self._messages = messages + [{"role": "assistant", "content": text}]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise AgentError(
                f"delegate reply was not JSON: {text[:300]}") from None
        if not isinstance(payload, dict):
            raise AgentError(
                f"delegate payload was not the requested JSON object: "
                f"{text[:300]}")
        return payload
