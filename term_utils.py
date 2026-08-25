from __future__ import annotations

import shutil
import textwrap
from typing import Optional

import rdflib

from optimal_lambda import LamTerm, LamVar, LamApp, LamAbs, lam_to_dict
from kg_store import EX, local_name, esc, _claim_predicate_label, load_lambda_db, save_lambda_db


def _through_fan(t):
    """A sharing fan-in is transparent to term-shape queries: both of its aux
    ports reach the one shared subject, so anything asking "what is here" gets
    the subject (reading_desc §4.2). Only the RENDERER draws the fan itself.
    """
    while (hasattr(t, "principal")
           and hasattr(t, "grey_cast") and hasattr(t, "black_cast")):
        t = t.principal
    return t


def _rightmost_var(t: LamTerm) -> Optional[LamVar]:
    """Walk the right spine to find the rightmost VAR of a sub-term."""
    t = _through_fan(t)
    while isinstance(t, LamApp):
        t = _through_fan(t.arg)
    return t if isinstance(t, LamVar) else None


def _rep_var(t: LamTerm) -> Optional[LamVar]:
    """Representative variable for claim-edge matching.

    LamVar  → itself
    LamAbs  → bound variable (the abstracted entity)
    LamApp  → rightmost var of the application
    """
    t = _through_fan(t)
    if isinstance(t, LamVar):
        return t
    if isinstance(t, LamAbs):
        return t.var
    return _rightmost_var(t)


def collect_app_edges(term: LamTerm) -> list[tuple[str, str]]:
    """Return (subj_iri, obj_iri) pairs for every APP node in the term (pre-order).

    Uses _rep_var so that LamAbs nodes contribute their bound variable as the
    representative entity, making edges involving abstractions visible.
    """
    edges: list[tuple[str, str]] = []
    seen: set[int] = set()

    def walk(t: LamTerm) -> None:
        if id(t) in seen:
            return                      # a shared subject is visited ONCE
        seen.add(id(t))
        t2 = _through_fan(t)
        if t2 is not t:
            walk(t2)
            return
        if isinstance(t, LamApp):
            rf = _rep_var(t.func)
            ra = _rep_var(t.arg)
            if rf and ra:
                edges.append((rf.iri, ra.iri))
            walk(t.func)
            walk(t.arg)
        elif isinstance(t, LamAbs):
            walk(t.body)
    walk(term)
    return edges


def collect_edge_claims(g: rdflib.Graph, term: LamTerm) -> list[dict]:
    """For each APP edge in term, collect all matching claims from the graph."""
    seen_claims: set[str] = set()
    result: list[dict] = []
    for subj_iri, obj_iri in collect_app_edges(term):
        subj = rdflib.URIRef(subj_iri)
        obj  = rdflib.URIRef(obj_iri)
        for claim in g.subjects(EX.subject, subj):
            if g.value(claim, EX.object) != obj:
                continue
            claim_iri = str(claim)
            if claim_iri in seen_claims:
                continue
            seen_claims.add(claim_iri)
            text = str(g.value(claim, EX.claimText) or "")
            result.append({
                "subj_iri": subj_iri,
                "obj_iri": obj_iri,
                "claim_iri": claim_iri,
                "claim_text": text,
            })
    return result


# ── question titles for abstractions ────────────────────────────────────────
#
# An abstraction IS a question (reading_desc §8.2): its negative form is a title,
# its positive form G(lam a.t) is what ontologies are built from. A rendered
# ontology shows the positive form, so the title — the thing a reader would
# recognise — is not visible unless it is looked up.
#
# The key is `str(term)`, which is exactly what `append_lambda_term` stores as
# `term_str`, so a saved question and an abstraction in an ontology that came
# from it produce the same key. Several questions may share one term (distinct
# titles over the same graph and asked entity are allowed), so the index maps to
# a LIST and the renderer shows the first with a count of the rest.

_ABS_TITLES: Optional[dict[str, list[str]]] = None


def abstraction_titles(refresh: bool = False) -> dict[str, list[str]]:
    """term_str -> [question title, ...] for every saved abstraction.

    Cached: the store has thousands of records and a render walks many nodes.
    Pass refresh=True after saving a new question.
    """
    global _ABS_TITLES
    if _ABS_TITLES is not None and not refresh:
        return _ABS_TITLES
    idx: dict[str, list[str]] = {}
    for rec in load_lambda_db():
        d = rec.get("term")
        if not isinstance(d, dict) or d.get("type") != "abs":
            continue
        key = rec.get("term_str")
        title = rec.get("chain_label")
        if not key or not title:
            continue
        idx.setdefault(key, []).append(title)
    _ABS_TITLES = idx
    return idx


_ABS_ORIGINS: Optional[dict[str, set[str]]] = None


def abstraction_origins(refresh: bool = False) -> dict[str, set[str]]:
    """term_str -> {origin, ...} for every saved abstraction.

    `origin` records WHO AUTHORED the question: 'ai' for the generated bulk,
    other values for questions written for a purpose. Several records may share
    one term (distinct titles over the same graph are allowed, §8.2), so a term
    maps to the SET of origins its records carry.
    """
    global _ABS_ORIGINS
    if _ABS_ORIGINS is not None and not refresh:
        return _ABS_ORIGINS
    idx: dict[str, set[str]] = {}
    for rec in load_lambda_db():
        d = rec.get("term")
        if not isinstance(d, dict) or d.get("type") != "abs":
            continue
        key = rec.get("term_str")
        if not key:
            continue
        idx.setdefault(key, set()).add(rec.get("origin") or "")
    _ABS_ORIGINS = idx
    return idx


def origins_of_abstraction(t: LamTerm) -> set[str]:
    """The origins recorded for this abstraction, empty if it is not saved."""
    return abstraction_origins().get(str(t), set())


def has_non_ai_question(term: LamTerm) -> bool:
    """Does `term` contain a saved question whose origin is NOT 'ai'?

    Used to rank the ontologies list: among members with the same number of
    fired rules, one built from a question somebody wrote for a purpose is more
    interesting than one from the generated bulk, so it is listed first.

    A question that is NOT SAVED counts as 'ai' here — it carries no authorship
    to prefer. An abstraction arising by reduction rather than by proposal is the
    usual such case.
    """
    def walk(x) -> bool:
        if isinstance(x, LamAbs):
            if any(o and o != "ai" for o in origins_of_abstraction(x)):
                return True
            return walk(x.body)
        if isinstance(x, LamApp):
            return walk(x.func) or walk(x.arg)
        if _is_fan(x):
            return walk(x.principal)
        return False
    return walk(term)


def title_for_abstraction(t: LamTerm) -> Optional[str]:
    """The question title for this abstraction, or None if it is not a saved one.

    An abstraction can arise inside an ontology by REDUCTION as well as by being
    proposed whole, so a miss is ordinary and simply means "no title to show".
    """
    titles = abstraction_titles().get(str(t))
    if not titles:
        return None
    if len(titles) == 1:
        return titles[0]
    # distinct titles over the same term are legitimate — name one, count the rest
    return f"{titles[0]}  (+{len(titles) - 1} more)"


def _occurs_free(t: LamTerm, iri: str) -> bool:
    """Does `iri` occur FREE in t? A binder for the same entity shadows it."""
    if isinstance(t, LamVar):
        return t.iri == iri
    if isinstance(t, LamApp):
        return _occurs_free(t.func, iri) or _occurs_free(t.arg, iri)
    if isinstance(t, LamAbs):
        if t.var.iri == iri:
            return False
        return _occurs_free(t.body, iri)
    return False


def inner_abstractions(t: LamTerm, _top: bool = True,
                       acc: Optional[list] = None) -> list:
    """Every abstraction node STRICTLY INSIDE t."""
    if acc is None:
        acc = []
    if isinstance(t, LamAbs):
        if not _top:
            acc.append(t)
        inner_abstractions(t.body, False, acc)
    elif isinstance(t, LamApp):
        inner_abstractions(t.func, False, acc)
        inner_abstractions(t.arg, False, acc)
    return acc


def check_question(term: LamTerm) -> Optional[str]:
    """Why `term` is not a well-formed question, or None if it is.

    Two invariants, both because ONLY SAVED QUESTIONS MAY BE USED DURING
    ENRICHMENT (§8.4) — so every abstraction reachable in the store has to be one
    a user actually asked:

      (1) THE BOUND VARIABLE MUST OCCUR FREE IN THE BODY. Otherwise the question
          claims to be about an entity its graph never mentions; and since
          [G(lam a.t)] == [a] (§8.2), enrichment would offer it as a candidate of
          that type even though reducing it can never place the asked entity.
          A shadowed occurrence does not count — an inner binder for the same
          entity rebinds it, leaving the outer binder binding nothing.

      (2) EVERY NESTED ABSTRACTION MUST ITSELF BE A SAVED QUESTION. An anonymous
          abstraction inside a stored term is material nobody asked for: it
          renders untitled and cannot be accounted for.
    """
    if not isinstance(term, LamAbs):
        return None                       # not a question; nothing to check
    if not _occurs_free(term.body, term.var.iri):
        return (f"the bound variable {term.var.label!r} does not occur free in "
                f"the body, so the question is about nothing")
    saved = abstraction_titles()
    for q in inner_abstractions(term):
        if str(q) not in saved:
            return (f"the nested abstraction {str(q)[:60]!r} is not itself a "
                    f"saved question")
    return None


def append_lambda_term(name: str, term: LamTerm, g: rdflib.Graph) -> None:
    """Save a question. Raises ValueError if it is not well formed.

    The check is here rather than at the call sites because this is the ONLY way
    material enters the question store, and enrichment draws its candidates from
    exactly that store.
    """
    why = check_question(term)
    if why is not None:
        raise ValueError(f"refusing to save {name!r}: {why}")
    records = load_lambda_db()
    records.append({
        "chain_label": name,
        "term": lam_to_dict(term),
        "term_str": str(term),
        "claims": collect_edge_claims(g, term),
    })
    save_lambda_db(records)
    abstraction_titles(refresh=True)      # the new title must be visible at once
    abstraction_origins(refresh=True)


def _term_type(t: LamTerm) -> Optional[str]:
    """Return the type IRI of term t under the rules:
      [var]       = var.iri
      [(a b)]     = [b]
      [(lam a) t] = a.iri   (the BOUND variable's type, not the body's)

    The third rule holds for an abstraction whether or not it is applied:
    abstracting RETYPES, and that is what makes "all abstractions of type A" a
    finite lookup for the ontology layer (reading_desc §8.2).
    """
    t = _through_fan(t)
    while isinstance(t, LamApp):
        if isinstance(_through_fan(t.func), LamAbs):
            return _through_fan(t.func).var.iri
        t = _through_fan(t.arg)
    if isinstance(t, LamVar):
        return t.iri
    if isinstance(t, LamAbs):
        return t.var.iri          # [(lam a) t] = [a], applied or not
    return None


def _leftmost_var(t: LamTerm) -> Optional[LamVar]:
    """Walk the left spine to find the leftmost VAR."""
    t = _through_fan(t)
    while isinstance(t, LamApp):
        t = _through_fan(t.func)
    return t if isinstance(t, LamVar) else None


def _top_claim_name(g: rdflib.Graph, term: LamTerm) -> str:
    """Return a predefined reading name from the top-level APP of term.

    Subject = leftmost var of the whole term (head entity).
    Object   = direct arg of the top APP (tail entity).
    Predicate = claim predicate between subject and object.
    """
    if not isinstance(term, LamApp):
        rv = _rep_var(term)
        return rv.label if rv else ""
    subj = _leftmost_var(term)
    obj = _rep_var(term.arg)
    if not subj or not obj:
        return ""
    pred = _claim_predicate_label(g, rdflib.URIRef(subj.iri), rdflib.URIRef(obj.iri))
    return f"'{subj.label}' '{pred}' '{obj.label}'"


def _wrap_claim(text: str, prefix: str) -> list[str]:
    """Return markup lines for one claim, every line starting at `prefix`."""
    cols = shutil.get_terminal_size((80, 24)).columns
    # 85% container, border(1)+padding(2) each side=6, scrollbar=1 → deduct 10
    available = max(20, int(cols * 0.85) - 10 - len(prefix))
    parts = textwrap.wrap(text, width=available) or [text]
    return [f"{prefix}[dim]{esc(p)}[/dim]" for p in parts]


def _is_fan(t) -> bool:
    """True for a sharing fan-in node (reading_state.LamFan).

    Tested duck-typed to keep term_utils free of a reading_state import: a fan is
    the only node with a `principal` plus the two cast slots.
    """
    return (hasattr(t, "principal")
            and hasattr(t, "grey_cast") and hasattr(t, "black_cast"))


def _find_shared(term: LamTerm) -> dict[int, int]:
    """Map id(subterm) -> share number for every subterm the GRAPH shares.

    THE COMPILER'S FAN-IN IS THE ONLY MEANING OF SHARING. `_Compiler._merge_free`
    builds a sharing fan-in for a variable exactly when that variable has FREE
    references in BOTH sides of an application. Two consequences, and the renderer
    must follow both or it draws structure the graph does not have:

      SHARING IS BY ENTITY, NOT BY OBJECT. The compiler keys its free-variable map
      on the IRI, so two DISTINCT LamVar objects naming one entity are merged by a
      fan just the same. Object identity is therefore not the criterion — it is
      neither necessary (distinct objects still share) nor sufficient (see next).

      A BINDER SCOPES SHARING, it does not forbid it. `_Compiler.abs` pops its own
      name out of the free map and wires it to the lambda fan's BLACK port — but
      that happens to the ALREADY-MERGED reference, so a bound variable used TWICE
      inside one body still gets a sharing fan first (verified: compiling
      `lam a.((a . b) . a)` yields an INTERNAL fan on `a`). What the binder does is
      END THE SCOPE: occurrences on opposite sides of it are merged separately, so
      they are never one shared node.

    So this counts occurrences PER ENTITY PER BINDING SCOPE, and marks the entity
    shared when two or more meet in the SAME scope — exactly when the compiler
    merges them into one fan.
    """
    # (entity iri, scope id) -> occurrence object ids, in encounter order.
    # The scope id distinguishes the free occurrences of an entity from those
    # bound by each enclosing lambda over it.
    occ: dict[tuple[str, int], list[int]] = {}
    seen_nodes: set[int] = set()

    def walk(t: LamTerm, scope: dict) -> None:
        if isinstance(t, LamVar):
            # scope.get -> the binder that captures this occurrence, or 0 for free
            occ.setdefault((t.iri, scope.get(t.iri, 0)), []).append(id(t))
            return
        key = id(t)
        if key in seen_nodes:
            return                              # a compound reached twice
        seen_nodes.add(key)
        if _is_fan(t):
            walk(t.principal, scope)
        elif isinstance(t, LamApp):
            walk(t.func, scope)
            walk(t.arg, scope)
        elif isinstance(t, LamAbs):
            walk(t.body, {**scope, t.var.iri: id(t)})

    walk(term, {})

    shared: dict[int, int] = {}
    n = 0
    for _key, ids in occ.items():
        if len(ids) < 2:
            continue
        n += 1
        for i in ids:                           # every occurrence carries the mark
            shared[i] = n
    return shared


_SHARE_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"


def _share_mark(n: int) -> str:
    return _SHARE_MARKS[n - 1] if 1 <= n <= len(_SHARE_MARKS) else f"({n})"


def _is_hole(t) -> bool:
    return type(t).__name__ == "_Hole"


def _render_ctx(ctx, lines: list, claims_by_edge: dict, prefix: str,
                shared: dict, expanded: set) -> None:
    """Render one fan branch's context. The HOLE renders as an edge back down to
    the fan's shared subject — NOT as a second copy of it (reading_desc §4.2).
    """
    if _is_hole(ctx):
        lines.append(f"{prefix}\u2514\u2500\u2500 [cyan]\u25bd[/cyan] [dim]the subject[/dim]")
        return
    _render_lam_body(ctx, lines, claims_by_edge, prefix, is_last=True,
                     shared=shared, expanded=expanded, hole_ok=True)


def _render_lam_body(term: LamTerm, lines: list, claims_by_edge: dict,
                     prefix: str = "", is_last: bool = True,
                     shared: dict[int, int] | None = None,
                     expanded: set[int] | None = None,
                     hole_ok: bool = False) -> None:
    """Render a lambda term as a box-drawing binary tree.

    claims_by_edge: (subj_iri, obj_iri) -> [claim_text], shown on APP nodes.
    prefix / is_last thread the │ connectors through siblings.

    SHARING IS SHOWN ONCE (reading_desc §4.2, I5). A subterm reached more than
    once carries a share mark ① and is expanded only at its FIRST occurrence;
    later occurrences render as a back-reference to that mark. Rendering it twice
    would depict duplication where the graph has one node.
    """
    shared = shared if shared is not None else {}
    expanded = expanded if expanded is not None else set()

    connector = "└── " if is_last else "├── "
    child_prefix = prefix + ("    " if is_last else "│   ")

    if _is_hole(term):
        # Inside a fan branch context: the edge back down to the fan's ONE shared
        # subject. Never a second copy of it (reading_desc §4.2, I5).
        lines.append(f"{prefix}{connector}[cyan]\u25bd[/cyan] [dim]the subject[/dim]")
        return

    key = id(term)
    share_no = shared.get(key)
    if share_no is not None:
        mark = f"[cyan]{_share_mark(share_no)}[/cyan] "
        # KEY ON THE SHARE NUMBER, not on the object: sharing is per ENTITY (see
        # _find_shared), so two distinct objects naming one entity are two
        # occurrences of ONE graph node and only the first may be expanded.
        if share_no in expanded:
            # a later use of one shared subgraph — point back, do not re-draw.
            # Name it by its TYPE, i.e. its rightmost leaf (reading_desc §7.4),
            # which is where the reader stands in that subterm.
            rv = _rep_var(term)
            lbl = esc(rv.label) if rv else "…"
            if isinstance(term, LamVar):
                lines.append(f"{prefix}{connector}{mark}[dim]{lbl}[/dim] [dim]shared[/dim]")
            else:
                lines.append(
                    f"{prefix}{connector}{mark}[dim]…{lbl}[/dim] [dim]shared[/dim]"
                )
            return
        expanded.add(share_no)
    else:
        mark = ""

    if _is_fan(term):
        # SHARING FAN-IN (§4.2). The subject is drawn ONCE, below the fan; the two
        # aux ports are shown as the fan's own upward edges, carrying the casts.
        # Nothing hangs off the ports themselves — they ARE the two occurrences.
        lines.append(f"{prefix}{connector}{mark}[cyan]▽[/cyan] [dim]shared[/dim]")
        ports = ((term.grey_ctx, term.grey_cast, "Q"),
                 (term.black_ctx, term.black_cast, "A"))
        for ctx, cast, tag in ports:
            cast_s = f"  [cyan]{esc(local_name(cast))}[/cyan]" if cast else ""
            lines.append(
                f"{child_prefix}├── [magenta]{tag}[/magenta]{cast_s}"
            )
            _render_ctx(ctx, lines, claims_by_edge, child_prefix + "│   ",
                        shared, expanded)
        # the ONE shared subject, drawn once, below the fan
        lines.append(f"{child_prefix}└── [cyan]▽[/cyan]")
        _render_lam_body(term.principal, lines, claims_by_edge,
                         child_prefix + "    ", is_last=True,
                         shared=shared, expanded=expanded)
    elif isinstance(term, LamVar):
        lines.append(f"{prefix}{connector}{mark}[b]{esc(term.label)}[/b]")
    elif isinstance(term, LamAbs):
        # An abstraction is a QUESTION. Show its TITLE when it is a saved one —
        # the bound variable alone names only the asked entity, not the question.
        title = title_for_abstraction(term)
        title_s = f"  [italic dim]{esc(title)}[/italic dim]" if title else ""
        lines.append(
            f"{prefix}{connector}{mark}[yellow]?[/yellow] [b]{esc(term.var.label)}[/b]"
            f"{title_s}"
        )
        _render_lam_body(term.body, lines, claims_by_edge, child_prefix,
                         is_last=True, shared=shared, expanded=expanded)
    elif isinstance(term, LamApp):
        rf = _rep_var(term.func)
        ra = _rep_var(term.arg)
        edge_claims = claims_by_edge.get((rf.iri, ra.iri), []) if rf and ra else []

        lines.append(f"{prefix}{connector}{mark}[yellow]·[/yellow]")
        _render_lam_body(term.func, lines, claims_by_edge, child_prefix,
                         is_last=False, shared=shared, expanded=expanded,
                         hole_ok=hole_ok)
        for ct in edge_claims:
            lines.extend(_wrap_claim(ct, child_prefix))
        _render_lam_body(term.arg, lines, claims_by_edge, child_prefix,
                         is_last=True, shared=shared, expanded=expanded,
                         hole_ok=hole_ok)


def _render_lam_root(term: LamTerm, claims_by_edge: dict, lines: list) -> None:
    """Render a lambda term from the root node (no leading connector).

    Shared subterms are detected up front and drawn once (see _render_lam_body).
    """
    shared = _find_shared(term)
    expanded: set[int] = set()
    if _is_fan(term):
        _render_lam_body(term, lines, claims_by_edge, prefix="",
                         is_last=True, shared=shared, expanded=expanded)
    elif isinstance(term, LamApp):
        rf = _rep_var(term.func)
        ra = _rep_var(term.arg)
        edge_claims = claims_by_edge.get((rf.iri, ra.iri), []) if rf and ra else []
        lines.append("[yellow]·[/yellow]")
        _render_lam_body(term.func, lines, claims_by_edge, prefix="",
                         is_last=False, shared=shared, expanded=expanded)
        for ct in edge_claims:
            lines.extend(_wrap_claim(ct, "  "))
        _render_lam_body(term.arg, lines, claims_by_edge, prefix="",
                         is_last=True, shared=shared, expanded=expanded)
    else:
        _render_lam_body(term, lines, claims_by_edge, prefix="",
                         is_last=True, shared=shared, expanded=expanded)


def _collect_var_iris(term: LamTerm) -> list[str]:
    """Return all variable IRIs appearing in a lambda term (depth-first, preserving order)."""
    if isinstance(term, LamVar):
        return [term.iri]
    if isinstance(term, LamApp):
        return _collect_var_iris(term.func) + _collect_var_iris(term.arg)
    if isinstance(term, LamAbs):
        return [term.var.iri] + _collect_var_iris(term.body)
    return []


def _make_ontology_term() -> LamTerm:
    """Build: (λb.(((λa.g(a·f))·b)·c)·d) · (λe.(e·h)).

    Every application node has a matching claim (7 total, all distinct):
      (a, f)  AHoJ  → BindingPocket    C04
      (g, f)  1FNP  → BindingPocket    C17
      (a, b)  AHoJ  → AHoJDB           C02  [λa redex]
      (b, c)  AHoJDB→ BindingPocket    C12+C13
      (c, d)  BindingPocket → AHoJDB   C05
      (b, e)  AHoJDB→ ApoForm          C06  [λb redex]
      (e, h)  ApoForm → CrypticSite    C08
    """
    EX_NS  = "https://ahoj-db.org/kg#"
    PDB = "https://www.rcsb.org/structure/"
    def v(iri: str, label: str) -> LamVar:
        return LamVar(iri=iri, label=label)
    a = v(EX_NS+"AHoJ",         "AHoJ")
    b = v(EX_NS+"AHoJDB",       "AHoJ-DB")        # bound in λb
    c = v(EX_NS+"BindingPocket","binding pocket")
    d = v(EX_NS+"AHoJDB",       "AHoJ-DB")        # free; same IRI as b
    e = v(EX_NS+"ApoForm",      "apo form")        # bound in λe
    f = v(EX_NS+"BindingPocket","binding pocket")  # free; same IRI as c
    g = v(PDB+"1FNP",        "1FNP")
    h = v(EX_NS+"CrypticSite",  "cryptic site")
    lam_a = LamAbs(var=a, body=LamApp(func=g, arg=LamApp(func=a, arg=f)))
    lam_b = LamAbs(var=b, body=LamApp(
        func=LamApp(func=LamApp(func=lam_a, arg=b), arg=c), arg=d))
    lam_e = LamAbs(var=e, body=LamApp(func=e, arg=h))
    return LamApp(func=lam_b, arg=lam_e)


def _make_beta_term() -> LamTerm:
    return _make_ontology_term()
