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


def append_lambda_term(name: str, term: LamTerm, g: rdflib.Graph) -> None:
    records = load_lambda_db()
    records.append({
        "chain_label": name,
        "term": lam_to_dict(term),
        "term_str": str(term),
        "claims": collect_edge_claims(g, term),
    })
    save_lambda_db(records)


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
    """Map id(subterm) -> share number for every subterm reached more than once.

    Retained for terms whose sharing is only object identity — e.g. anything
    built before fan-in nodes existed, or a term assembled directly as
    LamApp(t, t). A reading's own sharing is now carried by an explicit fan
    (§4.2) and needs no inference.
    """
    seen: set[int] = set()
    shared: dict[int, int] = {}

    def walk(t: LamTerm) -> None:
        key = id(t)
        if key in seen:
            if key not in shared:
                shared[key] = len(shared) + 1
            return                      # do NOT descend again
        seen.add(key)
        if _is_fan(t):
            walk(t.principal)
        elif isinstance(t, LamApp):
            walk(t.func)
            walk(t.arg)
        elif isinstance(t, LamAbs):
            walk(t.body)

    walk(term)
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
        if key in expanded:
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
        expanded.add(key)
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
        lines.append(
            f"{prefix}{connector}{mark}[yellow]?[/yellow] [b]{esc(term.var.label)}[/b]"
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
