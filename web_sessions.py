"""Live interactions, per browser — the state the loop needs between requests.

The interaction loop is stateful across many turns: a term being built, a
pointer set, a proposal waiting to be answered. HTTP is not, so something must
hold that between requests. This module is that something.

IN MEMORY, BY DECISION. Sessions live in a dict and die with the process. The
consequences are real and are not hidden: ONE REPLICA, sticky sessions, and a
pod restart loses every interaction in flight. It is the right trade for a
research prototype and the wrong one for anything that must not lose work.

    WHAT WOULD CHANGE IT. Everything here goes through `SessionRegistry`, and a
    `QuerySession` is serialisable by the same encoder the store uses. Backing
    this with Mongo is a new SessionRegistry, not a change to the web layer —
    which is why the interface exists at all rather than a bare dict.

EXPIRY. A browser that walks away leaves its interaction behind; without a
sweep those accumulate until the pod dies. Sessions older than `ttl` are
dropped on access, and `sweep()` can be called on a timer.

THE SESSION ID IS A COOKIE, NOT A USER. Users type a name and are not
authenticated; the cookie only says which browser tab an interaction belongs
to. Two people typing the same name are two sessions, and that is correct — the
name is a label on the record, not an identity.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from interaction_engine import Proposal, QuerySession


@dataclass
class LiveSession:
    """One browser's interaction in flight."""
    sid: str
    user: str
    session: Optional[QuerySession] = None
    # The proposal currently on screen; the options the user is choosing among
    # must be the ones the step was built from, so it is held rather than
    # rebuilt.
    pending: Optional[Proposal] = None
    # Set once the query is answered, so the result page can be re-shown.
    record_id: Optional[str] = None
    answer: str = ""
    created: float = field(default_factory=time.time)
    touched: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.touched = time.time()


class SessionRegistry:
    """Live sessions, keyed by an opaque cookie value.

    Thread-safe: uvicorn serves requests on a threadpool, and two requests from
    one browser can overlap (a double-clicked button), so the dict is guarded.
    """

    def __init__(self, ttl: float = 8 * 3600) -> None:
        self._sessions: dict[str, LiveSession] = {}
        self._lock = threading.Lock()
        self.ttl = ttl

    def new(self, user: str) -> LiveSession:
        sid = secrets.token_urlsafe(24)
        live = LiveSession(sid=sid, user=user)
        with self._lock:
            self._sessions[sid] = live
        return live

    def get(self, sid: Optional[str]) -> Optional[LiveSession]:
        if not sid:
            return None
        with self._lock:
            live = self._sessions.get(sid)
            if live is None:
                return None
            if time.time() - live.touched > self.ttl:
                del self._sessions[sid]
                return None
            live.touch()
            return live

    def drop(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def sweep(self) -> int:
        """Drop everything past its ttl. Returns how many went."""
        now = time.time()
        with self._lock:
            dead = [s for s, l in self._sessions.items()
                    if now - l.touched > self.ttl]
            for s in dead:
                del self._sessions[s]
        return len(dead)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
