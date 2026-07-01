from __future__ import annotations

import shutil
import textwrap
from typing import Optional

import rdflib

from optimal_lambda import LamTerm, LamVar, LamApp, LamAbs, lam_to_dict
from kg_store import EX, local_name, esc, _claim_predicate_label, load_lambda_db, save_lambda_db


def _rightmost_var(t: LamTerm) -> Optional[LamVar]:
    """Walk the right spine to find the rightmost VAR of a sub-term."""
    while isinstance(t, LamApp):
        t = t.arg
    return t if isinstance(t, LamVar) else None


def _rep_var(t: LamTerm) -> Optional[LamVar]:
    """Representative variable for claim-edge matching.

    LamVar  → itself
    LamAbs  → bound variable (the abstracted entity)
    LamApp  → rightmost var of the application
    """
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
    def walk(t: LamTerm) -> None:
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
      [(lam a) t] = a.iri   (beta-head: abstraction applied — type is bound var)
    """
    while isinstance(t, LamApp):
        if isinstance(t.func, LamAbs):
            return t.func.var.iri
        t = t.arg
    if isinstance(t, LamVar):
        return t.iri
    return None


def _leftmost_var(t: LamTerm) -> Optional[LamVar]:
    """Walk the left spine to find the leftmost VAR."""
    while isinstance(t, LamApp):
        t = t.func
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


def _render_lam_body(term: LamTerm, lines: list, claims_by_edge: dict,
                     prefix: str = "", is_last: bool = True) -> None:
    """Render a lambda term as a box-drawing binary tree.

    claims_by_edge: (subj_iri, obj_iri) -> [claim_text], shown on APP nodes.
    prefix / is_last thread the │ connectors through siblings.
    """
    connector = "└── " if is_last else "├── "
    child_prefix = prefix + ("    " if is_last else "│   ")

    if isinstance(term, LamVar):
        lines.append(f"{prefix}{connector}[b]{esc(term.label)}[/b]")
    elif isinstance(term, LamAbs):
        lines.append(f"{prefix}{connector}[yellow]?[/yellow] [b]{esc(term.var.label)}[/b]")
        _render_lam_body(term.body, lines, claims_by_edge, child_prefix, is_last=True)
    elif isinstance(term, LamApp):
        rf = _rep_var(term.func)
        ra = _rep_var(term.arg)
        edge_claims = claims_by_edge.get((rf.iri, ra.iri), []) if rf and ra else []

        lines.append(f"{prefix}{connector}[yellow]·[/yellow]")
        _render_lam_body(term.func, lines, claims_by_edge, child_prefix, is_last=False)
        for ct in edge_claims:
            lines.extend(_wrap_claim(ct, child_prefix))
        _render_lam_body(term.arg,  lines, claims_by_edge, child_prefix, is_last=True)


def _render_lam_root(term: LamTerm, claims_by_edge: dict, lines: list) -> None:
    """Render a lambda term from the root node (no leading connector)."""
    if isinstance(term, LamApp):
        rf = _rep_var(term.func)
        ra = _rep_var(term.arg)
        edge_claims = claims_by_edge.get((rf.iri, ra.iri), []) if rf and ra else []
        lines.append("[yellow]·[/yellow]")
        _render_lam_body(term.func, lines, claims_by_edge, prefix="", is_last=False)
        for ct in edge_claims:
            lines.extend(_wrap_claim(ct, "  "))
        _render_lam_body(term.arg, lines, claims_by_edge, prefix="", is_last=True)
    else:
        _render_lam_body(term, lines, claims_by_edge, prefix="", is_last=True)


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
