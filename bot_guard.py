"""Bot protection on query submit.

There is no authentication here: a user types a name and is believed. So what
needs protecting is not the data but the SPEND — every interaction step is a
paid delegation, and an open endpoint is a way to burn an API budget. That is
what this module is for, and it is why the guard sits on query submit (where
cost begins) rather than on the name form (where nothing is spent).

TWO LAYERS, because they fail differently:

    Turnstile   Cloudflare's challenge. Stops scripted abuse. Needs a secret,
                needs the network, and is OFF when unconfigured — a dev
                checkout must run without one.
    RateLimit   A per-IP ceiling, always on. Costs nothing, needs nothing, and
                catches the case Turnstile cannot: a real browser, solved once,
                then driven in a loop.

FAIL CLOSED, WITH ONE EXCEPTION. If Turnstile is configured and its verify call
fails — network down, Cloudflare unreachable — the submission is REFUSED. The
alternative (letting it through when the check breaks) turns any outage into an
open endpoint. The exception is that an unconfigured Turnstile is simply off,
which is not a failure but a deployment without it.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


class RateLimit:
    """A sliding window per key. Cheap, always on, no dependencies."""

    def __init__(self, limit: int = 20, window: float = 3600.0) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> Verdict:
        """Record a hit for `key` and say whether it is within the ceiling."""
        now = time.time()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                mins = int((self.window - (now - q[0])) / 60) + 1
                return Verdict(False, f"too many queries — try again in {mins} min")
            q.append(now)
            return Verdict(True)

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


class Turnstile:
    """Cloudflare Turnstile. Off when unconfigured."""

    def __init__(self, secret: str = "", site_key: str = "",
                 timeout: float = 5.0) -> None:
        self.secret = secret or ""
        self.site_key = site_key or ""
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.secret and self.site_key)

    def verify(self, token: str, remote_ip: str = "") -> Verdict:
        if not self.enabled:
            return Verdict(True, "turnstile not configured")
        if not token:
            return Verdict(False, "please complete the challenge")
        try:
            import httpx
            data = {"secret": self.secret, "response": token}
            if remote_ip:
                data["remoteip"] = remote_ip
            r = httpx.post(TURNSTILE_VERIFY_URL, data=data, timeout=self.timeout)
            body = r.json()
        except Exception:
            # FAIL CLOSED: an unreachable verifier must not become an open door.
            return Verdict(False, "could not verify the challenge — try again")
        if body.get("success"):
            return Verdict(True)
        return Verdict(False, "challenge failed — try again")


class Guard:
    """Both layers, in the order that costs least."""

    def __init__(self, turnstile: Optional[Turnstile] = None,
                 rate: Optional[RateLimit] = None) -> None:
        self.turnstile = turnstile or Turnstile()
        self.rate = rate or RateLimit()

    def check_submit(self, ip: str, token: str = "") -> Verdict:
        """May this IP start a query now?

        The rate limit runs FIRST: it is local and free, so a flood is rejected
        without a network call per attempt.
        """
        v = self.rate.check(ip or "unknown")
        if not v.ok:
            return v
        return self.turnstile.verify(token, remote_ip=ip)
