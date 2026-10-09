"""The web layer — server-rendered HTML over the interaction engine.

A thin shell. Every route does the same three things: find the live session,
call one engine method, render what the engine now says. No calculus lives here;
if a rule seems to be implemented in this file, it is in the wrong place.

    GET  /                 name form
    POST /start            take the name, open a session
    GET  /query            query form (Turnstile here, where spend begins)
    POST /query            guard, decompose, open the first subquery
    GET  /step             the current point: question + up to 4 options
    POST /choose           apply the chosen option
    POST /skip             case 4
    POST /resume           case 5, then answer
    GET  /result           the answer, the interaction, the question
    GET  /history          this name's past queries
    GET  /healthz          liveness, for k8s

WHY SERVER-RENDERED. The state is server-side by decision, and a step is a form
post: no client state to desynchronise, nothing to rebuild after a reload, and
the whole thing works without JavaScript except for the Turnstile widget.
"""

from __future__ import annotations

import html
import os
import re
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from answer_agent import EchoAnswerer, TransportAnswerer, answer_query
from bot_guard import Guard, RateLimit, Turnstile
from entity_index import EntityIndex
from interaction_engine import Entity, Option, Proposal, QuerySession
from interaction_store import InteractionStore, record_from_session
from interaction_state import lam_to_dict_shared
from web_sessions import SessionRegistry

COOKIE = "sl_session"

# What each case does, for the verbose panel.
CASE_LABEL = {
    1: "case 1 — contraction opt.1: app(ta, td), you MOVE to the answer",
}


# ── rendering ─────────────────────────────────────────────────────────────

def e(s) -> str:
    """Escape for HTML. `None` is empty — but 0 is NOT: pointer ids start at
    zero, and `str(s or "")` would silently blank the first pointer."""
    return html.escape("" if s is None else str(s))


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>{head}
<style>
  :root {{ --bg:#fbfaf8; --fg:#1a1a1a; --dim:#6b6b6b; --line:#e3e0da;
           --accent:#7a5c3e; --card:#fff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#17161a; --fg:#ece9e4; --dim:#9b968e; --line:#302d33;
             --accent:#c9a87c; --card:#201e24; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
    font:16px/1.6 ui-serif,Georgia,'Times New Roman',serif;
    padding:32px 16px; }}
  main {{ max-width:42rem; margin:0 auto; }}
  h1 {{ font-size:1.5rem; font-weight:600; margin:0 0 .25rem; }}
  .sub {{ color:var(--dim); font-size:.9rem; margin:0 0 2rem; }}
  .quote {{ border-left:3px solid var(--accent); padding:.4rem 0 .4rem .9rem;
    margin:1.5rem 0; font-size:1.05rem; }}
  .card {{ background:var(--card); border:1px solid var(--line);
    border-radius:10px; padding:1.1rem 1.2rem; margin:1rem 0; }}
  label {{ display:block; margin:.6rem 0 .3rem; color:var(--dim);
    font-size:.85rem; }}
  input[type=text], textarea {{ width:100%; padding:.7rem .8rem; font:inherit;
    border:1px solid var(--line); border-radius:8px;
    background:var(--bg); color:var(--fg); }}
  textarea {{ display:block; resize:vertical; min-height:4.5em;
    line-height:1.45; overflow:hidden; field-sizing:content; }}
  button {{ font:inherit; padding:.6rem 1.1rem; border-radius:8px;
    border:1px solid var(--line); background:var(--card); color:var(--fg);
    cursor:pointer; }}
  button.primary {{ background:var(--accent); border-color:var(--accent);
    color:#fff; }}
  button.opt {{ display:block; width:100%; text-align:left; margin:.5rem 0;
    padding:.8rem .9rem; }}
  button.opt:hover {{ border-color:var(--accent); }}
  .why {{ display:block; color:var(--dim); font-size:.85rem; margin-top:.2rem; }}
  .row {{ display:flex; gap:.6rem; flex-wrap:wrap; margin-top:1.2rem; }}
  .err {{ color:#a33; }}
  .term {{ font-family:ui-monospace,monospace; font-size:.85rem;
    color:var(--dim); word-break:break-all; }}
  .step {{ border-bottom:1px solid var(--line); padding:.7rem 0; }}
  .step:last-child {{ border-bottom:0; }}
  .ans > :first-child {{ margin-top:0; }}
  .ans > :last-child {{ margin-bottom:0; }}
  .ans h3, .ans h4, .ans h5, .ans h6 {{ font-size:1rem; margin:1.2rem 0 .4rem; }}
  .ans ul, .ans ol {{ padding-left:1.5rem; margin:.6rem 0; }}
  .ans li {{ margin:.35rem 0; }}
  .ans li > ul, .ans li > ol {{ margin:.25rem 0; }}
  .ans hr {{ border:0; border-top:1px solid var(--line); margin:1.2rem 0; }}
  .ans blockquote {{ margin:.8rem 0; padding:.2rem 0 .2rem .9rem;
    border-left:3px solid var(--line); color:var(--dim); }}
  .ans pre {{ background:var(--bg); border:1px solid var(--line);
    border-radius:6px; padding:.7rem .8rem; overflow-x:auto; }}
  .ans pre code {{ border:0; padding:0; background:none; }}
  .ans table {{ border-collapse:collapse; margin:.8rem 0; display:block;
    overflow-x:auto; font-size:.92rem; }}
  .ans th, .ans td {{ border:1px solid var(--line); padding:.35rem .6rem;
    text-align:left; vertical-align:top; }}
  .ans th {{ background:var(--bg); }}
  .ans code {{ font-family:ui-monospace,monospace; font-size:.9em;
    background:var(--bg); border:1px solid var(--line); border-radius:4px;
    padding:.05rem .3rem; }}
  a {{ color:var(--accent); }}
  .verbose {{ font-size:.85rem; }}
  .vrow {{ display:flex; gap:.8rem; padding:.45rem 0;
    border-bottom:1px solid var(--line); }}
  .vrow:last-child {{ border-bottom:0; }}
  .vk {{ flex:0 0 7.5rem; color:var(--dim); }}
  .verbose em {{ font-style:normal; color:var(--accent); }}
</style></head><body><main>{body}</main></body></html>"""


def page(title: str, body: str, head: str = "") -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=e(title), body=body, head=head))


# The delegate is asked for prose, but every model writes MARKDOWN anyway —
# bold names, numbered lists, the occasional heading. Rendered with `e()` alone
# that arrives on screen as literal `**Lorcaserin**`, so the answer panel reads
# like source code.
#
# WHY NOT A MARKDOWN LIBRARY. `requirements.txt` is deliberately short, and a
# renderer would also drag in a sanitiser: the answer is MODEL OUTPUT, so it
# cannot be trusted with raw HTML. Escaping first and formatting the escaped
# text afterwards is safe by construction — no tag the model writes can survive
# `html.escape`, and the only tags in the result are the ones added here.
_MD_INLINE = [
    # ordered longest-marker first, so `**` is not eaten by `*`
    (re.compile(r"\*\*(\S(?:.*?\S)?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"__(\S(?:.*?\S)?)__"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*(\S(?:.*?\S)?)\*(?![\w*])"), r"<em>\1</em>"),
    (re.compile(r"(?<![\w_])_(\S(?:.*?\S)?)_(?![\w_])"), r"<em>\1</em>"),
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
]

_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")

_MD_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_MD_NUMBER = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_MD_HEADING = re.compile(r"^\s*(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_MD_FENCE = re.compile(r"^\s*(```|~~~)")
_MD_QUOTE = re.compile(r"^\s*&gt;\s?(.*)$")          # `>` is escaped by now
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _md_inline(text: str) -> str:
    for pat, sub in _MD_INLINE:
        text = pat.sub(sub, text)
    # Only http(s) targets: the text is escaped, but `javascript:` is not HTML.
    return _MD_LINK.sub(r'<a href="\2" rel="noopener noreferrer">\1</a>', text)


def _md_indent(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


def _md_cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def markdown(text: str) -> str:
    """The subset of Markdown a delegate's answer actually uses.

    ESCAPING COMES FIRST — the input is escaped here, not by the caller, so
    there is no way to call this on unescaped text by mistake. What it handles:
    headings, rules, nested bullet and numbered lists, block quotes, tables,
    fenced code, bold, italic, inline code and http(s) links. Anything else is
    left as the escaped text it already is.

    LISTS SURVIVE BLANK LINES. Models space their numbered items apart and put
    sub-bullets or a further line under an item; closing the list at either
    would restart the numbering at 1 for every item. A list closes only when
    unindented text follows a blank line, or a block of another kind begins.
    """
    lines = e("" if text is None else text).split("\n")
    out: list[str] = []
    stack: list[tuple[int, str]] = []    # open lists: (indent, "ul" | "ol")
    blank = False                        # a blank line since the last content

    def close_to(indent: int) -> None:
        """Close every list nested deeper than `indent`."""
        while stack and stack[-1][0] > indent:
            out.append(f"</li></{stack.pop()[1]}>")

    def close_all() -> None:
        close_to(-1)

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        if not line.strip():
            blank = True
            continue
        was_blank, blank = blank, False

        if _MD_FENCE.match(line):
            close_all()
            code = []
            while i < len(lines) and not _MD_FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            i += 1                                   # the closing fence
            out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            continue

        h = _MD_HEADING.match(line)
        if h:
            close_all()
            n = min(len(h.group(1)) + 2, 6)      # page h1 is the title
            out.append(f"<h{n}>{_md_inline(h.group(2))}</h{n}>")
            continue

        if _MD_RULE.match(line):                 # before bullets: `* * *`
            close_all()
            out.append("<hr>")
            continue

        b = _MD_BULLET.match(line)
        n_ = _MD_NUMBER.match(line)
        if b or n_:
            indent = _md_indent(line)
            kind = "ul" if b else "ol"
            body = b.group(2) if b else n_.group(3)
            close_to(indent)
            if stack and stack[-1][0] == indent and stack[-1][1] != kind:
                out.append(f"</li></{stack.pop()[1]}>")
            if stack and stack[-1][0] == indent:
                out.append("</li><li>")
            else:
                # a new list — at top level, or nested inside the open item
                start = int(n_.group(2)) if n_ else 1
                attr = f' start="{start}"' if start != 1 else ""
                out.append(f"<{kind}{attr}><li>")
                stack.append((indent, kind))
            out.append(_md_inline(body.strip()))
            continue

        if stack and (_md_indent(line) > 0 or not was_blank):
            # more of the open item: an indented paragraph, or a lazy line
            close_to(_md_indent(line))
            if stack:
                sep = "<br><br>" if was_blank else "<br>"
                out.append(sep + _md_inline(line.strip()))
                continue

        close_all()

        if (line.lstrip().startswith("|") and i < len(lines)
                and _MD_TABLE_SEP.match(lines[i]) and "-" in lines[i]):
            head = _md_cells(line)
            i += 1
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_md_cells(lines[i]))
                i += 1
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{_md_inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>" + "".join(f"<td>{_md_inline(c)}</td>"
                                            for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue

        q = _MD_QUOTE.match(line)
        if q:
            quoted = [q.group(1)]
            while i < len(lines) and _MD_QUOTE.match(lines[i]):
                quoted.append(_MD_QUOTE.match(lines[i]).group(1))
                i += 1
            out.append("<blockquote>"
                       + "<br>".join(_md_inline(x.strip()) for x in quoted)
                       + "</blockquote>")
            continue

        out.append(f"<p>{_md_inline(line.strip())}</p>")
    close_all()
    return "".join(out)


def _short(exc: Exception, limit: int = 300) -> str:
    """An exception as one readable line.

    Provider errors arrive as a wall of JSON; the first line carries the part
    that says what to do about it.
    """
    msg = str(exc).strip().replace("\n", " ")
    return msg[:limit] + ("…" if len(msg) > limit else "")


def term_text(t, names: Optional[dict] = None) -> str:
    """A live term as readable text — entities by name, sharing marked.

    The same rendering the answer prompt uses, but over a term object rather
    than its document form, so the verbose panel can show the term AS IT GROWS.
    """
    from answer_agent import _term_text
    return _term_text(lam_to_dict_shared(t), names or {})


def verbose_panel(sess, prop: Optional[Proposal] = None) -> str:
    """Everything the calculus is doing, for --verbose.

    Five things, in the order they happen: the subqueries the query broke into,
    the point being clarified and what it maps to, which case that is, the term
    so far, and (once resumed) the interaction question.
    """
    if sess is None:
        return ""
    out = ['<div class="card verbose"><strong>The calculus</strong>']

    # 1. the subqueries, and which is being read
    out.append('<div class="vrow"><span class="vk">subqueries</span><span>')
    parts = []
    for s in sess.seeds:
        quote = sess.seed_quotes.get(s.iri, "")
        mark = ""
        if sess.current is not None and sess.current.seed.iri == s.iri:
            mark = " ← reading"
        elif any(c.seed.iri == s.iri for c in sess.closed):
            mark = " ✓ closed"
        elif s.iri not in sess.started:
            mark = " · not started"
        parts.append(f'“{e(quote)}” → <em>{e(s.label)}</em>{mark}')
    out.append("<br>".join(parts) + "</span></div>")

    # 2. the unclear point, and the entity it maps to
    if prop is not None and prop.case:
        retype = prop.retype.short() if prop.retype else "—"
        out.append(f'<div class="vrow"><span class="vk">unclear point</span>'
                   f'<span>“{e(prop.point)}” → <em>{e(retype)}</em></span></div>')
        # 3. which case
        out.append(f'<div class="vrow"><span class="vk">case</span>'
                   f'<span>{e(CASE_LABEL.get(prop.case, prop.case))}</span></div>')
        opts = "<br>".join(
            f"{i + 1}. {e(o.label)} → <em>"
            + e(o.entity.short() if o.entity else o.closed_name or "?")
            + "</em>"
            for i, o in enumerate(prop.options))
        out.append(f'<div class="vrow"><span class="vk">options</span>'
                   f'<span>{opts}</span></div>')

    # 4. the term so far, with the pointer set.
    # Names come from the session registry, so a retype shows as the entity's
    # LABEL rather than its raw iri.
    names = {iri: v.label for iri, v in getattr(sess.entities, "_vars", {}).items()}
    for s in sess.seeds:
        names.setdefault(s.iri, s.label)
    if prop is not None:
        # A retype names a question and need never have entered the registry,
        # so take its label from the proposal itself.
        if prop.retype:
            names.setdefault(prop.retype.iri, prop.retype.label)
        for o in prop.options:
            if o.entity is not None:
                names.setdefault(o.entity.iri, o.entity.label)
    if sess.current is not None:
        t = term_text(sess.current.term, names)
        ps = sess.current.pointers
        pts = ", ".join(
            f"p{p.pid}@{'.'.join(map(str, p.path)) or 'root'}"
            + (f"[{p.cast_type}]" if p.cast_type else "")
            + (" skipped" if p.skipped else "")
            for p in ps.pointers)
        out.append(f'<div class="vrow"><span class="vk">term</span>'
                   f'<span class="term">{e(t)}</span></div>')
        out.append(f'<div class="vrow"><span class="vk">pointers</span>'
                   f'<span class="term">P = {{{e(pts)}}}, act = '
                   f'{e(ps.act_pid)}</span></div>')

    for c in sess.closed:
        out.append(f'<div class="vrow"><span class="vk">closed {e(c.name)}</span>'
                   f'<span class="term">{e(term_text(c.term, names))} : '
                   f'{e(names.get(c.type_iri(), c.type_iri()))}</span></div>')

    if sess.lambda_list:
        out.append('<div class="vrow"><span class="vk">λ-list</span><span>'
                   + ", ".join(e(x.short()) for x in sess.lambda_list)
                   + "</span></div>")
    out.append("</div>")
    return "".join(out)


# ── the app ───────────────────────────────────────────────────────────────

def create_app(source=None, store=None, answerer=None, guard=None,
               entities=None, registry=None, verbose: bool = False) -> FastAPI:
    """Build the app. Everything it needs is injected, so tests pass fakes."""
    app = FastAPI(title="semantic lambda")
    app.state.verbose = verbose
    app.state.source = source
    app.state.store = store if store is not None else InteractionStore()
    app.state.answerer = answerer if answerer is not None else EchoAnswerer()
    app.state.guard = guard if guard is not None else Guard(
        turnstile=Turnstile(os.environ.get("TURNSTILE_SECRET", ""),
                            os.environ.get("TURNSTILE_SITE_KEY", "")),
        rate=RateLimit(limit=int(os.environ.get("RATE_LIMIT", "20"))))
    app.state.entities = entities if entities is not None else EntityIndex()
    app.state.sessions = registry if registry is not None else SessionRegistry()

    def live(request: Request):
        return app.state.sessions.get(request.cookies.get(COOKIE))

    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    # ── name ──────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        s = live(request)
        if s:
            return RedirectResponse("/query", status_code=303)
        return page("Who are you?", """
          <h1>Reading a question</h1>
          <p class="sub">Your question gets read one unclear point at a time,
          and answered as you meant it.</p>
          <form method="post" action="/start" class="card">
            <label for="name">Your name</label>
            <input type="text" id="name" name="name" autofocus required
                   maxlength="60" placeholder="how should we call you?">
            <div class="row"><button class="primary" type="submit">Start</button></div>
          </form>
          <p class="sub">No account, no password. The name just labels what you
          save.</p>""")

    @app.post("/start")
    def start(request: Request, name: str = Form("")):
        name = (name or "").strip()[:60]
        if not name:
            return RedirectResponse("/", status_code=303)
        s = app.state.sessions.new(name)
        r = RedirectResponse("/query", status_code=303)
        r.set_cookie(COOKIE, s.sid, httponly=True, samesite="lax", max_age=8 * 3600)
        return r

    # ── query ─────────────────────────────────────────────────────────────

    @app.get("/query", response_class=HTMLResponse)
    def query_form(request: Request, err: str = ""):
        s = live(request)
        if not s:
            return RedirectResponse("/", status_code=303)
        ts = app.state.guard.turnstile
        head = ('<script src="https://challenges.cloudflare.com/turnstile/v0/'
                'api.js" async defer></script>') if ts.enabled else ""
        widget = (f'<div class="cf-turnstile" data-sitekey="{e(ts.site_key)}"></div>'
                  if ts.enabled else "")
        error = f'<p class="err">{e(err)}</p>' if err else ""
        return page("Your question", f"""
          <h1>What do you want to know?</h1>
          <p class="sub">Asking as {e(s.user)} — <a href="/history">your past
          questions</a></p>
          {error}
          <form method="post" action="/query" class="card">
            <label for="q">Your question</label>
            <textarea id="q" name="query" rows="3" autofocus required
                      maxlength="500" placeholder="ask in your own words"></textarea>
            {widget}
            <div class="row"><button class="primary" type="submit">Read it</button></div>
          </form>
          <script>
            // The whole question stays in view: the box grows with the text.
            // Enter submits, as the one-line field did; Shift+Enter breaks a line.
            const q = document.getElementById("q");
            const fit = () => {{ q.style.height = "auto";
                                 q.style.height = q.scrollHeight + 2 + "px"; }};
            q.addEventListener("input", fit);
            q.addEventListener("keydown", ev => {{
              if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {{
                ev.preventDefault();
                q.form.requestSubmit();
              }}
            }});
            fit();
          </script>""", head=head)

    @app.post("/query")
    def query_submit(request: Request, query: str = Form(""),
                     cf_turnstile_response: str = Form("", alias="cf-turnstile-response")):
        s = live(request)
        if not s:
            return RedirectResponse("/", status_code=303)
        q = (query or "").strip()
        if not q:
            return RedirectResponse("/query", status_code=303)

        # THE GUARD SITS HERE, where spend begins.
        v = app.state.guard.check_submit(client_ip(request), cf_turnstile_response)
        if not v.ok:
            return RedirectResponse("/query?err=" + quote(v.reason, safe=""),
                                    status_code=303)

        sess = QuerySession(q, app.state.source, user=s.user)
        try:
            sess.start()
        except Exception as exc:
            # A delegate failure — a bad key, an endpoint down, a model that
            # does not exist — must be something the user can read and act on,
            # not a 500. It is also the most likely thing to go wrong on a
            # first run against a new provider.
            # URL-quote, do NOT html-escape: this goes in a query string, and
            # escaping first would put &#x27; into the URL.
            return RedirectResponse(
                "/query?err=" + quote(_short(exc), safe=""), status_code=303)
        s.session = sess
        s.answer = ""
        s.record_id = None
        nxt = sess.next_subquery()
        if nxt is not None:
            sess.open(nxt)
        return RedirectResponse("/step", status_code=303)

    # ── the loop ──────────────────────────────────────────────────────────

    def advance(sess: QuerySession) -> Optional[Proposal]:
        """The next point to put to the user, opening subqueries as needed.

        Returns None when the whole query is read — every subquery closed and
        nothing left unclear.
        """
        while True:
            if sess.current is None:
                nxt = sess.next_subquery()
                if nxt is None:
                    return None
                sess.open(nxt)
            prop = sess.propose()
            if prop.case != 0:
                return prop
            # nothing unclear here: (t, P) -> t, and on to the next subquery
            sess.close()

    @app.get("/step", response_class=HTMLResponse)
    def step(request: Request):
        s = live(request)
        if not s or s.session is None:
            return RedirectResponse("/query", status_code=303)
        sess = s.session
        # REUSE THE PENDING PROPOSAL. A GET must be idempotent: reloading the
        # page, or arriving here by redirect, must not ask the delegate again —
        # that would spend a call, and could put a different question on screen
        # than the one the user is about to answer.
        try:
            prop = s.pending or advance(sess)
        except Exception as exc:
            return page("Something went wrong", f"""
              <h1>The delegate could not be reached</h1>
              <p class="sub">in your question: {e(sess.query)}</p>
              <div class="card err">{e(_short(exc))}</div>
              <div class="row"><form method="get" action="/query">
                <button class="primary" type="submit">Try again</button>
              </form></div>""")
        if prop is None:
            return _finish(s)
        s.pending = prop

        here = sess.current.here()
        opts = []
        for i, o in enumerate(prop.options):
            why = f'<span class="why">{e(o.rationale)}</span>' if o.rationale else ""
            opts.append(f"""
              <form method="post" action="/choose">
                <input type="hidden" name="choice" value="{i}">
                <button class="opt" type="submit">{e(o.label)}{why}</button>
              </form>""")
        point = (f'<div class="quote">{e(prop.point)}</div>' if prop.point else "")
        vb = verbose_panel(sess, prop) if app.state.verbose else ""
        return page("A point to clarify", f"""
          <h1>{e(prop.question or 'What did you mean here?')}</h1>
          <p class="sub">in your question: {e(sess.query)}</p>
          {point}
          <div class="card">{''.join(opts)}</div>
          <form method="post" action="/skip" style="display:inline">
            <button type="submit">Skip this point</button></form>
          <form method="post" action="/resume" style="display:inline">
            <button type="submit">Answer it now</button></form>
          <p class="sub">Standing at {e(here.short() if here else '?')} ·
          {len(sess.closed)} read, {len(sess.seeds)} in all</p>
          {vb}""")

    @app.post("/choose")
    def choose(request: Request, choice: str = Form("0")):
        s = live(request)
        if not s or s.session is None or s.pending is None:
            return RedirectResponse("/query", status_code=303)
        try:
            i = int(choice)
            opt = s.pending.options[i]
        except (ValueError, IndexError):
            return RedirectResponse("/step", status_code=303)
        # A step that cannot be applied must not 500: the session is live and
        # the user has answered, so the only useful thing is to drop the bad
        # proposal and ask again from the same place.
        try:
            s.session.apply(s.pending, opt)
        except Exception as exc:
            s.pending = None
            return page("That step could not be applied", f"""
              <h1>That step could not be applied</h1>
              <p class="sub">in your question: {e(s.session.query)}</p>
              <div class="card err">{e(_short(exc))}</div>
              <div class="row"><form method="get" action="/step">
                <button class="primary" type="submit">Ask again</button>
              </form></div>""")
        s.pending = None
        return RedirectResponse("/step", status_code=303)

    @app.post("/skip")
    def skip(request: Request):
        s = live(request)
        if not s or s.session is None:
            return RedirectResponse("/query", status_code=303)
        s.session.skip(s.pending)
        s.pending = None
        return RedirectResponse("/step", status_code=303)

    @app.post("/resume")
    def resume(request: Request):
        s = live(request)
        if not s or s.session is None:
            return RedirectResponse("/query", status_code=303)
        s.session.resume()
        s.pending = None
        return RedirectResponse("/step", status_code=303)

    # ── the answer ────────────────────────────────────────────────────────

    def _finish(s):
        """Close what is open, save, answer, and show the result."""
        sess = s.session
        if sess.current is not None:
            sess.close()
        if not s.record_id:
            rec = record_from_session(sess)
            res = answer_query(rec, app.state.answerer, store=app.state.store)
            s.record_id = rec.id
            s.answer = res.answer
        return RedirectResponse("/result", status_code=303)

    @app.get("/result", response_class=HTMLResponse)
    def result(request: Request):
        s = live(request)
        if not s or not s.record_id:
            return RedirectResponse("/query", status_code=303)
        rec = app.state.store.get(s.record_id)
        if rec is None:
            return RedirectResponse("/query", status_code=303)

        names = {i: m.get("label", i) for i, m in rec.entities.items()}
        steps = []
        for inter in rec.interactions:
            for st in inter["steps"]:
                steps.append(f"""<div class="step">
                  <div>{e(st.get('point') or inter.get('subquery'))}</div>
                  <div class="sub" style="margin:0">{e(st.get('question'))} →
                  <strong>{e(st.get('answer'))}</strong></div></div>""")
        openv = ", ".join(e(names.get(i, i)) for i in rec.lambda_list)
        left = (f'<div class="card"><strong>Left open</strong><div class="sub"'
                f' style="margin:.3rem 0 0">{openv}</div></div>' if openv else "")
        read = (f'<div class="card"><strong>How it was read</strong>{"".join(steps)}'
                f'</div>' if steps else "")

        vb = ""
        if app.state.verbose:
            from answer_agent import _term_text
            rows = []
            for i in rec.interactions:
                rows.append(f'<div class="vrow"><span class="vk">{e(i["name"])}'
                            f'</span><span class="term">'
                            f'{e(_term_text(i["term"], names))} : '
                            f'{e(names.get(i.get("type"), i.get("type")))}'
                            f'</span></div>')
            if rec.question is not None:
                # THE INTERACTION QUESTION, as saved: lam E1...lam En.(t)
                rows.append('<div class="vrow"><span class="vk">λ-question</span>'
                            f'<span class="term">'
                            f'{e(_term_text(rec.question, names))}</span></div>')
            if rec.lambda_list:
                rows.append('<div class="vrow"><span class="vk">λ-list</span>'
                            '<span>'
                            + ", ".join(e(names.get(i, i)) for i in rec.lambda_list)
                            + "</span></div>")
            rows.append(f'<div class="vrow"><span class="vk">saved as</span>'
                        f'<span class="term">{e(rec.id)}</span></div>')
            vb = ('<div class="card verbose"><strong>What was saved</strong>'
                  + "".join(rows) + "</div>")

        return page("The answer", f"""
          <h1>The answer</h1>
          <p class="sub">asked by {e(rec.user)}</p>
          <div class="quote">{e(rec.query)}</div>
          <div class="card ans">{markdown(rec.answer)}</div>
          {read}{left}{vb}
          <div class="row">
            <form method="get" action="/query"><button class="primary"
              type="submit">Ask another</button></form>
            <form method="get" action="/history"><button type="submit">Past
              questions</button></form>
          </div>""")

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        s = live(request)
        if not s:
            return RedirectResponse("/", status_code=303)
        rows = app.state.store.for_user(s.user, limit=50)
        if not rows:
            items = '<p class="sub">Nothing yet.</p>'
        else:
            items = "".join(
                f'<div class="step"><div>{e(r.query)}</div>'
                f'<div class="sub" style="margin:0">{e(r.saved_at)}</div></div>'
                for r in rows)
        return page("Past questions", f"""
          <h1>Past questions</h1>
          <p class="sub">asked by {e(s.user)}</p>
          <div class="card">{items}</div>
          <div class="row"><form method="get" action="/query">
            <button class="primary" type="submit">Ask another</button></form></div>""")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "sessions": len(app.state.sessions)}

    return app
