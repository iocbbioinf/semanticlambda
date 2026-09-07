"""Reading browser — a terminal app that reads a query as a lambda term.

    $ python reading_browser.py
    > how does aspirin reduce inflammation

The user puts a query; a DELEGATE proposes the entities and the options. Every
choice the user makes is a READING STEP applied to R = (G(t), Pr) — see
`reading_session` for the mapping of the three interaction kinds onto contraction
(options 1 and 2) and reflection.

Two delegates, same interface:

    reading_mock.MockAgent   THE DEFAULT — no Claude call, no cost, no CLI
                             needed. Nonsense as knowledge, well formed as
                             readings, and it reaches all three step kinds.
    reading_agent.ReadingAgent   `claude -p`, with `--claude`.

Rendering is deliberately Claude-Code-shaped: a prompt line, streamed status
lines while the delegate is working, boxed option lists, and a running view of
the reading being built.

Options:   --verbose, -v     show the calculus: the term as it grows, the
                             pointer set, which reading step each choice makes,
                             the `t` view of the whole reading, and — under
                             --claude — what each delegation cost
           --per-step        skip the clarification phase: ask the delegate
                             afresh at every step (the older, costlier mode)
           --points N        how many ambiguities to enumerate up front (6)
           --claude          delegate to Claude Code instead of the mock
           --model NAME      model for --claude (default: sonnet)
           --seed N          vary the mock (default: derived from the query)

Commands:  1..n     take that reading — the interaction step
           a        ask a question — write it, choose the entity it is about;
                    the question is saved as `lam a.t` and a new reading starts
                    standing in it
           p        switch context (offered once a reflection has split the
                    reading; the two contexts grow independently)
           s        skip this place
           o        the ontology set — the models in which this reading is
                    valid; a number opens one in BOTH forms, original (as
                    proposed) and reduced (after the firings)
           t        show the reading — term, tree, pointers, steps  (--verbose)
           e        the entity store — uses and aliases              (--verbose)
           resume   save the question and all its readings, and stop
           quit     leave without saving
"""

from __future__ import annotations

import itertools
import shutil
import sys
import threading
import time
from typing import Optional

from reading_agent import AgentError, Entity, ReadingAgent
from reading_mock import MockAgent
from reading_session import Reading, ReadingSession
from term_utils import _term_type

# ── styling ───────────────────────────────────────────────────────────────────

_TTY = sys.stdout.isatty()

# --verbose shows the CALCULUS behind the interaction: the term as it grows, the
# pointer set, which reading step each choice performs, the `t` view of the whole
# reading, and what each delegation cost. Without it the app shows only what the
# user is choosing between — the reading is still built exactly the same way,
# just not narrated.
VERBOSE = False


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def dim(s: str) -> str:      return _c("2", s)
def bold(s: str) -> str:     return _c("1", s)
def orange(s: str) -> str:   return _c("38;5;209", s)
def green(s: str) -> str:    return _c("38;5;114", s)
def blue(s: str) -> str:     return _c("38;5;110", s)
def mauve(s: str) -> str:    return _c("38;5;140", s)
def red(s: str) -> str:      return _c("38;5;174", s)


KIND_STYLE = {
    "A": (orange, "A", "entity understood several ways", "contraction · option 1"),
    "B": (mauve,  "B", "relation understood several ways", "reflection"),
    "C": (blue,   "C", "how this place was reached", "contraction · option 2"),
}


class Spinner:
    """A working indicator, in the shape Claude Code uses."""

    FRAMES = "⋅∙●∙"

    def __init__(self, label: str) -> None:
        self.label = label
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self):
        if _TTY:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            print(f"  {self.label}…")
        return self

    @staticmethod
    def _width() -> int:
        """Columns available. Only the label clip needs this — erasing uses
        clear-to-EOL, so a wrong guess here cannot leave whitespace behind."""
        try:
            return max(shutil.get_terminal_size((80, 24)).columns, 20)
        except Exception:
            return 80

    def _spin(self) -> None:
        t0 = time.monotonic()
        # The label can be long (an entity label is free text), so clip it to the
        # terminal: a spinner line that wraps cannot be erased by one \r, and its
        # tail is what was left dangling after the line below it was printed.
        w = self._width()
        label = self.label
        room = w - 14
        if room > 8 and len(label) > room:
            label = label[:room - 1] + "…"
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            el = time.monotonic() - t0
            sys.stdout.write(
                f"\r\033[2K  {orange(frame)} {dim(label)} {dim(f'({el:.0f}s)')}")
            sys.stdout.flush()
            time.sleep(0.12)

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            # Erase with the ANSI clear-to-end-of-line, not a run of spaces:
            # spaces have to guess the width, and on a wide terminal a guess
            # that is too long IS the trailing whitespace it was meant to remove
            # (and one too short leaves the tail behind).
            sys.stdout.write("\r\033[2K" if _TTY else "\r")
            sys.stdout.flush()
        return False


# ── rendering ─────────────────────────────────────────────────────────────────

def banner(mock: bool, clarify: bool = True) -> None:
    print()
    print(f"  {orange('◆')} {bold('reading browser')}  "
          f"{dim('— a query, read as a term')}")
    if mock:
        print(dim("  backend: ") + mauve("mock") +
              dim("  — no Claude call, no cost; --claude to delegate for real"))
    else:
        print(dim("  backend: ") + green("claude -p") +
              dim("  — the query is delegated to Claude Code"))
    print(dim("  every choice you make is a reading step"))
    if clarify:
        print(dim("  first phase: ") + orange("clarify the query") +
              dim("  — each step settles one ambiguity in what you asked"))
    cmds = ("  at each step: a ask · o ontologies · p switch context · "
            "s skip · t reading · e entities · resume · quit") if VERBOSE else (
            "  at each step: a ask a question · o ontologies · "
            "p switch context · s skip · resume · quit")
    print(dim(cmds))
    print()


def show_term(reading: Reading) -> None:
    """The reading as it stands: term, pointer set, entities. VERBOSE only."""
    if not VERBOSE:
        return
    print()
    print(f"  {dim('reading')} {bold(reading.seed.short())}")
    print(f"  {dim('term')}     {green(str(reading.term))}")
    act = reading.act_pointer()
    marks = []
    for p in reading.pointers.pointers:
        here = reading.pointer_entities.get(p.pid)
        tag = f"p{p.pid}"
        if p.is_reflected():
            tag += f"·{_short(p.cast_type)}"
        if here is not None:
            tag += f"@{here.short()}"
        marks.append(bold(f"[{tag}]") if act and p.pid == act.pid
                     else dim(f" {tag} "))
    print(f"  {dim('Pr')}       {' '.join(marks)}   "
          f"{dim(f'|Pr| = {len(reading.pointers)}')}")
    if reading.used:
        print(f"  {dim('entities')} " +
              dim(", ".join(e.short() for e in reading.used)))
    print()


def _rule(width: int = 74) -> str:
    return dim("─" * width)


def _plain_step(line: str) -> str:
    """A step line with the calculus stripped out.

    The stored line carries both what the user did and how it was built —
    "[A] contraction opt.1 — moved → X  [a asks, b answers]". Without --verbose
    only the first half is shown; the reading is built identically either way.
    """
    text = line
    if text.startswith("[") and "] " in text:
        text = text.split("] ", 1)[1]
    # the trailing bracket is the type/pointer annotation
    if "  [" in text and text.rstrip().endswith("]"):
        text = text[:text.rindex("  [")]
    for noise in ("contraction opt.1 — ", "contraction opt.2 — ",
                  "reflection — "):
        text = text.replace(noise, "")
    return text.strip()


def render_reading(reading: Reading, lines: list[str]) -> None:
    """The reading drawn as a tree, one node per line.

    An application is drawn as its two children under a `·` node; a sharing
    fan-in as `▽` with its two OCCURRENCES beneath it — both descending to the
    one shared subject, which is drawn once and marked where it is reached again
    (reading_desc §4.2: "occurs twice in the term but exists once in the graph").
    """
    from optimal_lambda import LamApp, LamVar
    from reading_state import LamFan

    act = reading.act_pointer()
    by_path = {}
    for p in reading.pointers.pointers:
        by_path.setdefault(tuple(p.path), []).append(p)

    def marks(path: tuple) -> str:
        out = []
        for p in by_path.get(path, []):
            tag = f"p{p.pid}"
            if p.cast_type:
                tag += f"·{_short(p.cast_type)}"
            out.append(bold(orange(f"◀ {tag}")) if act and p.pid == act.pid
                       else dim(f"◀ {tag}"))
        return "  " + " ".join(out) if out else ""

    seen: dict[int, str] = {}

    def walk(t, path: tuple, prefix: str, branch: str, under_fan: bool = False) -> None:
        here = prefix + branch
        # A subject reached a second time is the SAME node — say so rather than
        # drawing it twice, which would misrepresent the sharing.
        #
        # A bare VARIABLE is the one case to draw again: an entity reused across
        # steps is one node, but it reads as the entity itself wherever it turns
        # up, and collapsing it to "shared with…" hides which entity is there.
        # Under a FAN-IN that reasoning does not apply — both branches ARE the
        # one subject, which is the whole content of the reflection — so there
        # the second occurrence is always marked.
        if id(t) in seen and (under_fan or not isinstance(t, LamVar)):
            what = seen[id(t)]
            lines.append(f"{here}{dim('↺ shared — ' + what)}{marks(path)}")
            return
        child_prefix = prefix + ("   " if branch.startswith("└") else
                                 "│  " if branch else "")
        if isinstance(t, LamApp):
            seen[id(t)] = "the application above"
            lines.append(f"{here}{dim('·')}{marks(path)}")
            walk(t.func, path + (0,), child_prefix, "├─ ")
            walk(t.arg, path + (1,), child_prefix, "└─ ")
        elif isinstance(t, LamFan):
            seen[id(t)] = "the fan above"
            casts = []
            if t.grey_cast:
                casts.append(f"grey ▸ {_short(t.grey_cast)}")
            if t.black_cast:
                casts.append(f"black ▸ {_short(t.black_cast)}")
            lines.append(f"{here}{mauve('▽ fan-in')}  "
                         f"{dim(' · '.join(casts))}{marks(path)}")
            # The subject exists ONCE: name it so the second branch can say what
            # it shares with, rather than drawing it again.
            # Name the subject. A compound subject has no label of its own, so
            # fall back to its own rendering — "the subject" says nothing.
            subj = getattr(t.principal, "label", None) or str(t.principal)
            seen.setdefault(id(t.principal), f"one subject: {subj}")
            # `branch()` REBUILDS its term on every call, so the two must be held
            # at once. Walking `t.branch(0)` and then `t.branch(1)` lets the first
            # be collected and its id() REUSED by the second, which then reads as
            # "shared with" the first — sharing reported where there is none.
            grey, black = t.branch(0), t.branch(1)
            walk(grey, path + (0,), child_prefix, "├─ ", True)
            walk(black, path + (1,), child_prefix, "└─ ", True)
        elif isinstance(t, LamVar):
            label = getattr(t, "label", None) or _short(t.iri)
            # A variable is drawn wherever it occurs (see above), but say when it
            # is the SAME node turning up again — that is the sharing, and it is
            # invisible otherwise.
            again = dim("  ↺ same node") if id(t) in seen else ""
            seen.setdefault(id(t), f"the entity {label}")
            lines.append(f"{here}{green(label)}  {dim(_short(t.iri))}{again}"
                         f"{marks(path)}")
        else:
            lines.append(f"{here}{dim(str(t))}{marks(path)}")

    walk(reading.term, (), "", "")


def show_term_modal(reading: Reading, session) -> None:
    """A full view of the reading, held until the user dismisses it.

    `t` used to print and fall straight through to the next step, so the term
    flashed past. This holds the screen, and — importantly — the caller then
    re-presents THE SAME options rather than asking the delegate again.
    """
    lines: list[str] = []
    print()
    print("  " + _rule())
    print(f"  {bold('the reading so far')}"
          f"   {dim('query:')} {dim(session.query)}")
    print("  " + _rule())
    print(f"  {dim('seed')}     {bold(reading.seed.short())}")
    print(f"  {dim('term')}     {green(str(reading.term))}")
    print(f"  {dim('type')}     {dim(_short(_term_type(reading.term)))}"
          f"   {dim('— [app(a,b)] = [b]')}")
    print()

    render_reading(reading, lines)
    for ln in lines:
        print("  " + ln)

    print()
    act = reading.act_pointer()
    print(f"  {dim('pointers')} {dim(f'|Pr| = {len(reading.pointers)}')}"
          f"   {dim('(◀ marks where each stands;')} "
          f"{bold(orange('orange'))} {dim('is actPtr)')}")
    for p in reading.pointers.pointers:
        here = reading.pointer_entities.get(p.pid)
        flag = bold(orange("actPtr")) if act and p.pid == act.pid else dim("      ")
        cast = f"  {dim('cast')} {mauve(_short(p.cast_type))}" if p.cast_type else ""
        spent = dim("  (exhausted)") if p.pid in reading.exhausted else ""
        print(f"    {flag}  {dim(f'p{p.pid}')}  "
              f"{dim('path')} {dim(str(tuple(p.path)) if p.path else '()')}  "
              f"{here.short() if here else dim('?')}"
              f"  {dim(p.origin)}{cast}{spent}")

    if reading.used:
        print()
        print(f"  {dim('entities')} " +
              ", ".join(green(e.short()) for e in reading.used))

    if reading.steps:
        print()
        print(f"  {dim('steps')}")
        for i, line in enumerate(reading.steps, 1):
            print(f"    {dim(f'{i}.')} {line}")

    if session.closed:
        print()
        print(f"  {dim('closed readings')}")
        for c in session.closed:
            print(f"    {dim('·')} {c.name}  {green(str(c.term))}")

    print()
    print("  " + _rule())
    try:
        input(f"  {dim('enter to go back to the options')} ")
    except (EOFError, KeyboardInterrupt):
        print()


def report_merges(session) -> None:
    """Say when a proposed name resolved to an entity already held.

    A merge changes what gets built — the two names become ONE node — so it is
    shown rather than done silently.
    """
    for name, kept in session.entities.take_merges():
        # Only worth saying when the two names actually READ differently. The
        # delegate often sends an id that differs from the label while the label
        # is unchanged, and "“X” is X" is noise, not information.
        if name.strip().lower() == kept.strip().lower():
            continue
        print(f"  {dim('↺')} {dim(f'“{name}” is')} {bold(kept)}"
              f"{dim(' — same entity, one node')}")


def show_entities(session, reading=None) -> None:
    """The entity store: what this session is made of."""
    ents = session.entities.all()
    print()
    print("  " + _rule())
    print(f"  {bold('entities')}   {dim(f'{len(ents)} distinct')}")
    print("  " + _rule())
    if not ents:
        print(dim("  none yet"))
        return
    in_reading = {e.iri for e in reading.used} if reading is not None else set()
    for e in ents:
        here = orange("●") if e.iri in in_reading else dim("·")
        uses = dim(f"×{e.uses}") if e.uses > 1 else "   "
        print(f"   {here} {bold(e.label)}  {uses}  {dim(e.iri)}")
        if e.gloss:
            print(f"       {dim(e.gloss)}")
        if e.aliases:
            # The other names this entity arrived under — the merges, made
            # visible after the fact.
            print(f"       {dim('also proposed as: ' + ', '.join(e.aliases))}")
    if reading is not None:
        print()
        print(f"  {orange('●')} {dim('in the reading being built')}")
    print()


def ask_yes_no(question: str, default: bool = False) -> bool:
    """A y/n prompt. Anything unrecognised — or EOF — takes the default."""
    suffix = dim("[y/N]") if not default else dim("[Y/n]")
    try:
        raw = input(f"{question} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not raw:
        return default
    return raw[0] == "y"


def show_ontologies(reading: Reading, session) -> None:
    """The ontology set — the models in which this reading is valid (§8).

    An ontology is a hypothesis about what the user is doing; the set is every
    hypothesis still consistent with the reading. It shrinks by REFUTATION and
    grows by ENRICHMENT, so as reading continues the survivors are better
    approximations.
    """
    onts = list(reading.ontologies)
    print()
    print("  " + _rule())
    print(f"  {bold('ontologies')}   {dim(f'{len(onts)} in the set')}")
    print("  " + _rule())
    if not onts:
        print(dim("    none"))
        return

    for i, o in enumerate(onts, 1):
        if o.is_reading:
            # §8.1b: the reading is a member of its own set. It qualifies
            # trivially and never fires — the degenerate model.
            head = f"{dim('the reading itself')}  {dim('— never fires')}"
        else:
            fired = (green(f"fired ×{o.fired}") if o.fired
                     else dim("silent so far"))
            head = f"{bold('a model')}  {fired}"
        print(f"    {orange(str(i))}  {head}")
        print(f"        {green(str(o.term)[:66])}")
    print()
    # Only the reading left means no available material accounts for what the
    # user is doing — meaningful, not an error (§8.8).
    if all(o.is_reading for o in onts):
        print("  " + dim("only the reading remains — no abstraction "
                         "available accounts for it"))
    st = reading.ont_stats
    if st and VERBOSE:
        print(f"  {dim('last step:')} " + dim(" · ".join(
            f"{k} {v}" for k, v in st.items() if v)))
    print(dim("    a number for the detail · enter to go back"))

    try:
        raw = input(f"\n  {orange('❯')} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if raw.isdigit() and 1 <= int(raw) <= len(onts):
        show_ontology_detail(onts[int(raw) - 1], reading)


def show_ontology_detail(o, reading: Reading) -> None:
    """One ontology, in BOTH forms — as proposed and as reduced.

    The two differ because an ontology is a HYPOTHESIS and `term` is that
    hypothesis after the reading has consumed part of it:

      ORIGINAL  the graph as PROPOSED, with every enrichment substitution in
                place but nothing reduced. Only this shows which questions the
                model actually claimed — rule 1 CONSUMES the abstraction it
                fires on, so the reduced term has lost it.
      REDUCED   what is left after the firings the reading approved. Reduction
                replays the reading: contraction is R1, reflection is R4.
    """
    print()
    print("  " + _rule())
    kind = ("the reading itself" if o.is_reading else "a model of this reading")
    print(f"  {bold('ontology')}   {dim(kind)}")
    print("  " + _rule())

    print(f"  {bold('original')}  {dim('— as proposed, before any reduction')}")
    print(f"    {green(str(o.origin_term))}")
    print()
    print(f"  {bold('reduced')}   {dim('— after the firings the reading approved')}")
    print(f"    {green(str(o.term))}")
    if not o.is_reduced:
        print(f"    {dim('(nothing has fired here, so the two are the same)')}")

    print()
    print(f"  {dim('fired')}     {o.fired}  "
          f"{dim('— how often this model’s hypotheses paid off')}")
    if o.pointers:
        print(f"  {dim('pointers')}  " + dim(", ".join(
            f"p{pid}→{path if path else '()'}"
            for pid, path in sorted(o.pointers.items()))))
        print(f"  {dim('          (they correspond elementwise to the reading’s)')}")
    print()
    try:
        input(f"  {dim('enter to go back')} ")
    except (EOFError, KeyboardInterrupt):
        print()


def _stored_questions() -> dict:
    """Every question in the shared store, as qid -> title.

    Read once per process: the store holds hundreds, and re-reading it on every
    keystroke of the picker would make typing crawl.
    """
    global _STORED_Q
    if _STORED_Q is None:
        try:
            from term_utils import qid_titles
            _STORED_Q = dict(qid_titles())
        except Exception:
            _STORED_Q = {}
    return _STORED_Q


_STORED_Q = None


def pick_entity(reading: Reading, session, prompt: str):
    """Choose an entity by TYPING to filter, as the KG browser's search box did.

    Type any text to narrow the list (words are matched independently, so more
    words narrow rather than exclude); type a number to take that entity. The
    entities of the current reading are shown first and marked, since asking
    about one of them needs no contraction.

    Returns the chosen `Entity`, or None if cancelled.
    """
    from incremental_select import select

    here_iris = {e.iri for e in reading.used}
    # Questions already asked are offered alongside entities: a question names a
    # SUBTYPE of its entity's type (reading_desc §2), so it is a legitimate thing
    # to ask a further question of — that is what makes questions form a tree.
    #
    # BOTH this session's and the STORE's. The store is where every question ever
    # asked lives — this app's and the KG browser's alike — so a reading can be
    # built on a question asked long before, which is the point of keeping them.
    asked_before = [Entity(iri=q.qid, label=q.title,
                           gloss=f"a question asked of {q.asked.short()}")
                    for q in session.questions]
    seen_q = {q.iri for q in asked_before}
    for qid, title in _stored_questions().items():
        if qid not in seen_q:
            asked_before.append(Entity(iri=qid, label=title,
                                       gloss="a question in the store"))

    # This session's own material, in the order the user is likeliest to want it.
    session_pool = list(reading.used)
    session_pool += [Entity(iri=e.iri, label=e.label, gloss=e.gloss)
                     for e in session.entities.all() if e.iri not in here_iris]
    session_qids = {q.qid for q in session.questions}
    session_pool += [q for q in asked_before if q.iri in session_qids]

    def ranked(query: str) -> list[Entity]:
        if not query:
            # UNFILTERED, show only what this session is made of. The store holds
            # hundreds of questions from earlier work; listing them all would
            # bury the entity the user is almost certainly after. They are one
            # keystroke away.
            return sorted(session_pool,
                          key=lambda e: e.iri not in here_iris)[:8]

        from entity_store import search_score
        seen, pool = set(), []
        for e in session_pool + asked_before:
            if e.iri not in seen:
                seen.add(e.iri)
                pool.append(e)
        scored = [(max(search_score(e.label, query),
                       search_score(e.iri.split(":", 1)[-1].replace("-", " "),
                                    query)), e)
                  for e in pool]
        hits = [(sc, e) for sc, e in scored if sc > 0]
        # Session material outranks the store at equal score: it is what the
        # user has in hand.
        in_session = {e.iri for e in session_pool}
        hits.sort(key=lambda x: (-x[0], x[1].iri not in in_session,
                                 x[1].label.lower()))
        return [e for _sc, e in hits][:8]

    def render(e: Entity, selected: bool) -> str:
        from question_tree import is_question_type
        mark = orange("▸ ") if selected else "  "
        label = e.short()
        # Stored titles can be long; keep a row to one line.
        if len(label) > 56:
            label = label[:55] + "…"
        name = bold(label) if selected else label
        tag = ""
        if is_question_type(e.iri):
            # A question, not a plain entity (§2). Say which are already in hand
            # and which come from the store, since only the latter were asked in
            # some earlier session.
            tag = mauve("  ?") + (dim(" stored") if e.iri not in session_qids
                                  else dim(" asked here"))
        elif e.iri in here_iris:
            tag = dim("  (in this reading)")
        return f"{mark}{name}{tag}"

    return select(prompt, ranked, render,
                  empty_note=dim("nothing matches"))


def ask_a_question(reading: Reading, session) -> bool:
    """Ask a question of an entity: write the text, then choose what it is about.

    A question is an ABSTRACTION `lam a.t` (reading_desc §2) — the reading built
    so far as the body, the chosen entity as the binder. It names a SUBTYPE of
    that entity's type, and the new reading starts standing IN the question.

    The entity may be any the session knows, not only those in this reading; one
    that is not yet in the body is contracted in first, since a question whose
    bound variable does not occur free would be "about nothing".
    """
    print()
    try:
        title = input(f"  {bold('your question')} {dim('(blank to cancel)')}\n"
                      f"  {orange('❯')} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not title:
        return False

    asked = pick_entity(reading, session, "what is the question about?")
    if asked is None:
        return False
    qid, _ = session.ask_question(reading, title, asked)
    print()
    print(f"  {green('✓')} asked {bold(title)} {dim('of')} {bold(asked.short())}")
    if VERBOSE:
        q = session.questions[-1]
        print(f"    {dim('abstraction:')} {green(str(q.term))}")
        print(f"    {dim('names subtype:')} {dim(_short(qid))}")
    print(f"  {green('◆')} reading on from {bold(title)}")
    return True


def _pointer_line(reading: Reading, p, act) -> str:
    """One pointer, described as a place the user could stand.

    Quietly, a pointer is named by the entity it stands at plus the CONTEXT it
    carries — the cast a reflection put on that occurrence — since that is the
    choice being made. --verbose adds the machinery behind it: the pointer's id
    and which aux port of the fan it sits on.
    """
    here = reading.pointer_entities.get(p.pid)
    at = bold(here.short()) if here else dim("?")
    mark = bold(orange("▸")) if act and p.pid == act.pid else " "

    if not VERBOSE:
        # The cast IS the context's reading of the subject, so it is what
        # distinguishes the two places — not calculus, the actual choice.
        cast = (f"{dim('  context:')} {mauve(_short(p.cast_type))}"
                if p.cast_type else "")
        spent = dim("  (nothing left here)") if p.pid in reading.exhausted else ""
        return f"{mark} {at}{cast}{spent}"

    bits = []
    if p.cast_type:
        # The context's own reading of the subject: what was cast onto THIS
        # occurrence by the reflection that made it (§4.2).
        bits.append(f"{dim('reflected as')} {mauve(_short(p.cast_type))}")
    if p.origin.startswith("reflect"):
        side = "grey · left-up" if p.origin.endswith("left") else "black · right-up"
        bits.append(dim(side))
    elif p.origin == "init":
        bits.append(dim("the root"))
    if p.pid in reading.exhausted:
        bits.append(dim("exhausted"))
    tail = ("  " + dim(" · ").join(bits)) if bits else ""
    return f"{mark} {dim(f'p{p.pid}')}  {at}{tail}"


def choose_pointer(reading: Reading, session) -> bool:
    """Let the user pick which pointer to stand at — which context to continue in.

    Reflection is the only step that grows Pr (§4.2), and its two pointers are
    genuinely independent positions: this is where the user says which of them
    the reading continues from.
    """
    ptrs = list(reading.pointers.pointers)
    act = reading.act_pointer()
    if len(ptrs) < 2:
        print(dim("  only one pointer in this reading — nowhere else to stand"))
        return False

    print()
    count = f"  {dim(f'|Pr| = {len(ptrs)}')}" if VERBOSE else ""
    print(f"  {bold('where do you want to continue?')}{count}")
    for i, p in enumerate(ptrs, 1):
        print(f"    {orange(str(i))}  {_pointer_line(reading, p, act)}")
    print(f"    {dim('c')}  {dim('cancel')}")

    valid = {str(i) for i in range(1, len(ptrs) + 1)} | {"c"}
    while True:
        try:
            raw = input(f"\n  {orange('❯')} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if raw == "c":
            return False
        if raw in valid:
            p = ptrs[int(raw) - 1]
            if p.pid == (act.pid if act else None):
                print(dim("  already standing there"))
                return False
            session.select_pointer(reading, p.pid)
            if VERBOSE:
                here = reading.pointer_entities.get(p.pid)
                cast = (f"  {dim('reflected as')} {mauve(_short(p.cast_type))}"
                        if p.cast_type else "")
                print(f"  {green('✓')} now standing at {dim(f'p{p.pid}')} "
                      f"{bold(here.short() if here else '?')}{cast}")
            return True
        print(dim(f"    choose one of: {', '.join(sorted(valid))}"))


def show_step_header(kind: str, reading: Reading, entities: list[str]) -> None:
    """Which of A/B/C this is, and the entities it uses."""
    style, letter, what, calculus = KIND_STYLE[kind]
    if not VERBOSE:
        # The prompt below already puts the question in the user's own terms;
        # naming the KIND of step is calculus, so it says nothing extra here.
        return
    print()
    print(f"  {style('●')} {style(bold(f'step {letter}'))} {dim('·')} {what}")
    print(f"    {dim('reading step:')} {style(calculus)}")
    print(f"    {dim('standing at:')}  "
          f"{bold(reading.act_entity().short() if reading.act_entity() else '?')}")
    if entities:
        print(f"    {dim('entities:')}     {', '.join(entities)}")


def show_cost(agent, only_if_new: bool = False) -> None:
    """What the delegation just cost and how long it took.

    Only in --verbose, and only when there is something to show: the mock keeps
    every counter at 0.0, so an ordinary run stays silent. Costs are the
    `total_cost_usd` the `claude -p` envelope reports per call — the same figure
    /cost totals for a Claude Code session — accumulated over this reading
    session's delegations.

    THE TIMING BREAKDOWN MATTERS as much as the cost. `api` is what the CLI
    reports as time spent on the request; the rest of the wall clock is process
    startup, paid for spawning `claude -p` at all. Seeing that gap is what
    justifies the clarification phase batching: the overhead is per-PROCESS, so
    one call for many steps beats one call per step on time as well as money.
    """
    if not VERBOSE:
        return
    # `last_*` still holds the PREVIOUS call's figures when this step delegated
    # nothing, so reprinting them would read as if the step had cost that again.
    # Callers that may not have delegated pass only_if_new.
    if only_if_new:
        return
    last, total = agent.last_cost_usd, agent.total_cost_usd
    wall = getattr(agent, "last_wall_s", 0.0)
    if not total and not wall:
        return
    bits = [f"this step ${last:.4f}", f"session ${total:.4f}"]
    if wall:
        api = getattr(agent, "last_api_s", 0.0)
        overhead = max(wall - api, 0.0)
        t = f"took {wall:.1f}s"
        if api:
            t += f" (api {api:.1f}s + startup {overhead:.1f}s)"
        bits.append(t)
        tw = getattr(agent, "total_wall_s", 0.0)
        if tw and abs(tw - wall) > 0.05:
            bits.append(f"session {tw:.1f}s")
    print(f"  {dim('/cost  ' + '  ·  '.join(bits))}")


def named(ent) -> str:
    """`label — gloss`, or just the label when there is no gloss.

    The label is deliberately terse, so the gloss is what makes it legible; but
    appending an empty one leaves a name with trailing separator and dead space,
    which is what a bare `opening a reading from X  ` looked like.
    """
    gloss = (getattr(ent, "gloss", "") or "").strip()
    name = bold(ent.short())
    if not gloss or gloss.lower() == ent.short().lower():
        return name
    return f"{name}  {dim(gloss)}"


def show_prep_time(seconds: float, delegated: bool) -> None:
    """How long preparing this step took, and whether it cost a delegation.

    The spinner shows elapsed time while it runs but wipes the line on exit, so
    nothing survives to say how long a step actually took. That matters here:
    the whole point of the clarification phase is that most steps are served
    from the batched plan and should be INSTANT, so a step that suddenly waits
    on `claude -p` is worth seeing as such rather than being mistaken for the
    app being slow.
    """
    if not VERBOSE:
        return
    if delegated:
        print(f"  {dim(f'/time  next step prepared in {seconds:.1f}s')}"
              f"  {dim('· delegated to claude -p')}")
    elif seconds >= 0.05:
        print(f"  {dim(f'/time  next step prepared in {seconds:.2f}s')}"
              f"  {dim('· from the plan, no call')}")
    else:
        print(f"  {dim(f'/time  next step prepared in {seconds*1000:.0f}ms')}"
              f"  {dim('· from the plan, no call')}")


def _short(iri: Optional[str]) -> str:
    if not iri:
        return "?"
    return iri.split(":", 1)[-1].split("/")[-1].split("#")[-1]


def ask_choice(prompt: str, labels: list[str], extra: dict[str, str]) -> str:
    """Numbered menu. Returns "1".."n" or one of `extra`'s keys."""
    print()
    if prompt:
        print(f"  {bold(prompt)}")
    for i, lab in enumerate(labels, 1):
        print(f"    {orange(str(i))}  {lab}")
    for key, lab in extra.items():
        print(f"    {dim(key)}  {dim(lab)}")
    valid = {str(i) for i in range(1, len(labels) + 1)} | set(extra)
    while True:
        try:
            raw = input(f"\n  {orange('❯')} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"
        if raw in valid:
            return raw
        print(dim(f"    choose one of: {', '.join(sorted(valid))}"))


# ── the loop ──────────────────────────────────────────────────────────────────

class Browser:
    def __init__(self, agent, clarify: bool = True, points: int = 6) -> None:
        self.agent = agent
        self.session: Optional[ReadingSession] = None
        # The clarification phase is the DEFAULT first phase: the reading settles
        # what the query MEANS before anything is answered. --per-step restores
        # the older behaviour, where each step costs its own delegation.
        self.clarify = clarify
        self.points = points

    @property
    def is_mock(self) -> bool:
        return getattr(self.agent, "model", "") == "mock"

    # ── entry ─────────────────────────────────────────────────────────────

    def run(self) -> int:
        banner(self.is_mock, self.clarify)
        try:
            query = input(f"  {orange('❯')} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not query or query in ("quit", "exit"):
            return 0

        self.session = ReadingSession(query, self.agent)

        # THE FIRST PHASE: settle what was asked. One batched call enumerates the
        # query's ambiguities; every step afterwards is served from it locally, so
        # clarifying costs about one delegation however many steps it takes.
        if self.clarify:
            try:
                t0 = time.monotonic()
                with Spinner("reading the query" if self.is_mock
                             else "finding what is ambiguous in the query"):
                    plan = self.session.start_clarifying(points=self.points)
                plan_s = time.monotonic() - t0
            except AgentError as e:
                print(f"  {red('✗')} {e}")
                return 1
            # This is the ONE call the phase pays for, so its wait is the wait
            # that matters — every step after it should come back instantly.
            if VERBOSE and plan_s >= 0.05:
                print(f"  {dim(f'/time  the plan took {plan_s:.1f}s')}"
                      f"  {dim('· one call for the whole phase')}")
            show_cost(self.agent)
            known = self.session.entities_loaded
            if known:
                print("  " + dim("◆ ") +
                      dim(f"{known} entities known from earlier sessions"))
            print(f"  {green('✓')} {dim('the query is about')} "
                  f"{bold(plan.root.short())}")
            n = len(plan.points)
            kinds = "".join(pt.kind for pt in plan.points)
            print(f"  {green('✓')} {dim('found')} {bold(str(n))} "
                  f"{dim('point' + ('' if n == 1 else 's') + ' to clarify')}"
                  f"  {dim('[' + kinds + ']')}")
            if VERBOSE:
                for pt in plan.points:
                    print(f"      {dim('·')} {dim('[' + pt.kind + ']')} "
                          f"{pt.quote or dim('(unquoted)')}  "
                          f"{dim(f'{len(pt.options)} options')}")
            report_merges(self.session)
            seed = plan.root
        else:
            try:
                with Spinner("reading the query" if self.is_mock
                             else "delegating the query to Claude Code"):
                    seeds = self.session.start()
            except AgentError as e:
                print(f"  {red('✗')} {e}")
                return 1
            show_cost(self.agent)

            known = self.session.entities_loaded
            if known:
                print("  " + dim("◆ ") +
                      dim(f"{known} entities known from earlier sessions"))
            print(f"  {green('✓')} {dim('entities proposed:')} "
                  f"{', '.join(bold(s.short()) for s in seeds)}")
            report_merges(self.session)
            self.session.seed_cursor = 0
            seed = self.session.next_unread_seed()
            if seed is None:
                print(f"  {red('✗')} nothing to read")
                return 1

        try:
            return self.loop(seed)
        except (EOFError, KeyboardInterrupt):
            print()
            self.do_resume()
            return 0

    def loop(self, seed: Entity) -> int:
        s = self.session
        reading = s.open_reading(seed)
        print(f"\n  {green('◆')} opening a reading from {named(seed)}")
        show_term(reading)

        while True:
            try:
                at = reading.act_entity()
                # A step served from the plan does no I/O, so say "finding"
                # rather than "asking for" — the wording tells the user whether
                # this wait is a delegation or not before the timing confirms it.
                planned = s.plan is not None
                verb = "finding" if (self.is_mock or planned) else "asking for"
                calls_before = getattr(self.agent, "calls", 0)
                t0 = time.monotonic()
                with Spinner(f"{verb} the next step at "
                             f"{at.short() if at else '?'}"):
                    prop = s.propose(reading)
                prep_s = time.monotonic() - t0
                delegated = getattr(self.agent, "calls", 0) > calls_before
            except AgentError as e:
                print(f"  {red('✗')} {e}")
                return 1
            show_prep_time(prep_s, delegated)
            show_cost(self.agent, only_if_new=not delegated)

            if prop.kind == "none":
                # No interaction step from this pointer. Try the reading's other
                # pointers; if none is left, this reading is done and we open a
                # new one from an entity that has no reading yet.
                moved = s.mark_exhausted(reading)
                if moved is not None:
                    print("  " + dim("no step here — moving to another "
                                     "pointer of this reading"))
                    continue
                print(f"  {dim('no interaction step remains in this reading')}")
                cr = s.close_current()
                if cr is not None:
                    print(f"  {green('✓')} closed reading "
                          f"{bold(cr.name)}  {dim(str(cr.term))}")
                nxt = s.next_unread_seed()
                if nxt is None:
                    print(f"\n  {dim('every entity has a reading — resuming')}")
                    self.do_resume()
                    return 0
                reading = s.open_reading(nxt)
                print(f"\n  {green('◆')} opening a reading from {named(nxt)}")
                show_term(reading)
                continue

            # Render which of A/B/C this is, and the entities it uses.
            ents = []
            for o in prop.options:
                if prop.kind == "B":
                    ents += [o.entity_a.short(), o.entity_b.short()]
                elif o.entity is not None:
                    ents.append(o.entity.short())
            seen, uniq = set(), []
            for e in ents:
                if e not in seen:
                    seen.add(e)
                    uniq.append(e)
            # WHAT THE USER CHOOSES BETWEEN is how a point of their query is
            # understood — so the label carries the whole choice, and the SENSE
            # it maps to is calculus, shown only under --verbose. Without the
            # plan (per-step mode) the entity is the choice, so it still shows.
            labels = []
            for o in prop.options:
                if prop.kind == "B":
                    ents = (f"  {mauve(o.entity_a.short())} {dim('(question) ⋅')} "
                            f"{mauve(o.entity_b.short())} {dim('(answer)')}")
                else:
                    tgt = (f"«{o.reading_name}»" if o.reading_name
                           else o.entity.short())
                    ents = f"  {mauve(tgt)}"
                if self.clarify and not VERBOSE and not o.reading_name:
                    ents = ""
                labels.append(f"{o.label}{ents}"
                              + (f"\n       {dim(o.rationale)}" if o.rationale else ""))

            # Present THIS proposal until the user picks or leaves. `t` opens the
            # modal and comes back here — it must NOT fall through to the outer
            # loop, which would ask the delegate for a fresh proposal and throw
            # away the options on screen (and, on --claude, pay for them again).
            choice = None
            while choice is None:
                show_step_header(prop.kind, reading, uniq)
                report_merges(s)
                extra = {"a": "ask a question", "s": "skip this place"}
                extra["o"] = "ontologies"
                if VERBOSE:
                    extra["t"] = "show the reading"
                    extra["e"] = "entities"
                # Only offer the pointer picker when there is a choice to make —
                # which, since only reflection grows Pr, means after a split.
                if len(reading.pointers) > 1:
                    extra["p"] = (f"switch context "
                                  f"({len(reading.pointers)} open)")
                extra["r"] = "resume (save and stop)"
                extra["q"] = "quit without saving"
                # The words of the query at issue: the point being clarified is
                # the user's OWN phrase, so showing it is what makes the step
                # legible without any calculus.
                if prop.note.startswith("clarifying: "):
                    print(f"\n  {dim('in your query:')} "
                          f"{orange(prop.note[len('clarifying: '):])}")
                picked = ask_choice(
                    prop.prompt or "which reading do you take?", labels, extra)
                if picked == "t":
                    show_term_modal(reading, s)
                    continue
                if picked == "e":
                    show_entities(s, reading)
                    continue
                if picked == "o":
                    show_ontologies(reading, s)
                    continue
                if picked == "a":
                    # Asking CLOSES this reading and opens one standing in the
                    # question, so the loop must pick the new reading up.
                    if ask_a_question(reading, s):
                        choice = "asked"
                        break
                    continue
                if picked == "p":
                    # Moving re-points the reading; the options on screen belong
                    # to where the user WAS, so go back for a fresh proposal.
                    if choose_pointer(reading, s):
                        choice = "moved"
                        break
                    continue
                choice = picked

            if choice == "asked":
                reading = s.current
                show_term(reading)
                continue
            if choice == "moved":
                continue

            if choice in ("q", "quit"):
                print(dim("  left without saving"))
                return 0
            if choice == "r":
                self.do_resume()
                return 0
            if choice == "s":
                if s.mark_exhausted(reading) is None:
                    print(dim("  no other pointer here"))
                continue

            opt = prop.options[int(choice) - 1]
            line = s.apply(reading, prop, opt)
            print(f"\n  {green('✓')} {line if VERBOSE else _plain_step(line)}")
            show_term(reading)

            if prop.kind == "B":
                # The split just happened: the two occurrences are independent
                # positions, and the reading continues in whichever the user
                # picks. Offer the choice here, while it is the live question,
                # rather than leaving them on whichever side reflection selected.
                act = reading.act_pointer()
                twin = next((p for p in reading.pointers.pointers
                             if p.reflect_id is not None and act is not None
                             and p.reflect_id == act.reflect_id
                             and p.pid != act.pid), None)
                if twin is not None:
                    print(f"  {dim('the reading split — you are in context')} "
                          f"{mauve(_short(act.cast_type))}{dim(';')} "
                          f"{dim('the other is')} "
                          f"{mauve(_short(twin.cast_type))}")
                    if ask_yes_no("  continue in the other context instead?"):
                        s.select_pointer(reading, twin.pid)
                        if VERBOSE:
                            here = reading.pointer_entities.get(twin.pid)
                            print(f"  {green('✓')} now in context "
                                  f"{mauve(_short(twin.cast_type))} at "
                                  f"{bold(here.short() if here else '?')}")

    # ── resume ────────────────────────────────────────────────────────────

    def do_resume(self) -> None:
        """"resume for now means — save init question and all its readings"."""
        s = self.session
        if s is None:
            return
        if s.current is not None:
            s.close_current()
        if not s.closed:
            print(dim("  nothing to save"))
            return
        from reading_store import save_session
        path = save_session(s)

        # Entities persist too, so the next session knows what this one named.
        ent_path = s.save_entities()
        print()
        print(f"  {green('✓')} saved {bold(str(len(s.entities)))} entit"
              f"{'y' if len(s.entities) == 1 else 'ies'}")
        if VERBOSE:
            print(f"  {dim('→')} {dim(str(ent_path))}")

        # Questions go to the SHARED question store, which is where enrichment
        # draws its candidates from — not into the session file.
        if s.questions:
            n, failed = s.save_questions()
            print()
            print(f"  {green('✓')} saved {bold(str(n))} question(s) to the "
                  f"question store")
            for q in s.questions:
                print(f"      {dim('·')} {q.title}  {dim('of')} {q.asked.short()}")
            for why in failed:
                print(f"      {red('✗')} {dim(why)}")
        print()
        print(f"  {green('✓')} saved {bold(str(len(s.closed)))} reading(s) for "
              f"{bold(s.query)}")
        for c in s.closed:
            # The term itself is the calculus; without --verbose say how many
            # steps the reading took instead. Both are saved either way.
            detail = (green(str(c.term)) if VERBOSE
                      else dim(f"{len(c.steps)} step"
                               f"{'' if len(c.steps) == 1 else 's'}"))
            print(f"      {dim('·')} {c.name}  {detail}")
        print(f"  {dim('→')} {dim(str(path))}")
        if self.agent.total_cost_usd:
            print(f"  {dim(f'delegation cost: ${self.agent.total_cost_usd:.4f}')}")
        print()


def _arg(argv: list[str], name: str) -> Optional[str]:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    global VERBOSE
    VERBOSE = "--verbose" in argv or "-v" in argv

    # The MOCK is the default: an ordinary run costs nothing and needs no CLI.
    # `--claude` delegates for real.
    if "--claude" in argv or "--real" in argv:
        agent = ReadingAgent(model=_arg(argv, "--model") or "sonnet")
    else:
        raw = _arg(argv, "--seed")
        agent = MockAgent(seed=int(raw) if raw and raw.lstrip("-").isdigit()
                          else None)
    pts = _arg(argv, "--points")
    return Browser(agent,
                   clarify="--per-step" not in argv,
                   points=int(pts) if pts and pts.isdigit() else 6).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
