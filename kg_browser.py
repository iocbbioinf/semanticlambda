#!/usr/bin/env python3
"""Knowledge Graph Browser — terminal app for exploring the AHoJ RDF knowledge graph."""

from __future__ import annotations

import json
import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import pyperclip
import rdflib
from rdflib.namespace import RDFS, OWL, SKOS, DCTERMS
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static
from textual.containers import VerticalScroll, ScrollableContainer, Horizontal, Vertical

TTL_FILE = Path("/home/marek/uochb/ch/paperskg/ahoj-db.ttl")
LAMBDA_DB = Path(__file__).parent / "data" / "lambda_terms.json"
READINGS_DB = Path(__file__).parent / "data" / "readings.json"

EX = rdflib.Namespace("https://ahoj-db.org/kg#")
PROV_ACTIVITY = rdflib.URIRef("http://www.w3.org/ns/prov#Activity")


# ── Lambda term data model ────────────────────────────────────────────────────

@dataclass
class LamVar:
    """A variable (leaf) identified by its IRI."""
    iri: str
    label: str

    def to_dict(self) -> dict:
        return {"type": "var", "iri": self.iri, "label": self.label}

    @staticmethod
    def from_dict(d: dict) -> "LamVar":
        return LamVar(iri=d["iri"], label=d["label"])

    def __str__(self) -> str:
        return self.label


@dataclass
class LamApp:
    """Application: (func arg)."""
    func: "LamTerm"
    arg: "LamTerm"

    def to_dict(self) -> dict:
        return {"type": "app", "func": lam_to_dict(self.func), "arg": lam_to_dict(self.arg)}

    @staticmethod
    def from_dict(d: dict) -> "LamApp":
        return LamApp(func=lam_from_dict(d["func"]), arg=lam_from_dict(d["arg"]))

    def __str__(self) -> str:
        f = str(self.func)
        a = str(self.arg)
        # wrap in parens unless already atomic
        f_s = f if isinstance(self.func, LamVar) else f"({f})"
        a_s = a if isinstance(self.arg, LamVar) else f"({a})"
        return f"{f_s} · {a_s}"


@dataclass
class LamAbs:
    """Abstraction: λvar.body"""
    var: LamVar
    body: "LamTerm"

    def to_dict(self) -> dict:
        return {"type": "abs", "var": self.var.to_dict(), "body": lam_to_dict(self.body)}

    @staticmethod
    def from_dict(d: dict) -> "LamAbs":
        return LamAbs(var=LamVar.from_dict(d["var"]), body=lam_from_dict(d["body"]))

    def __str__(self) -> str:
        return f"λ{self.var}.({self.body})"


LamTerm = Union[LamVar, LamApp, LamAbs]


def lam_to_dict(t: LamTerm) -> dict:
    return t.to_dict()


def lam_from_dict(d: dict) -> LamTerm:
    kind = d["type"]
    if kind == "var":
        return LamVar.from_dict(d)
    if kind == "app":
        return LamApp.from_dict(d)
    if kind == "abs":
        return LamAbs.from_dict(d)
    raise ValueError(f"Unknown lambda term type: {kind}")


def lam_subst(term: LamTerm, var: LamVar, value: LamTerm) -> LamTerm:
    """Substitute value for all free occurrences of var (matched by IRI) in term."""
    if isinstance(term, LamVar):
        return value if term.iri == var.iri else term
    if isinstance(term, LamApp):
        return LamApp(func=lam_subst(term.func, var, value),
                      arg=lam_subst(term.arg, var, value))
    if isinstance(term, LamAbs):
        if term.var.iri == var.iri:
            return term  # bound variable shadows substitution
        return LamAbs(var=term.var, body=lam_subst(term.body, var, value))
    return term


def beta_step(term: LamTerm) -> Optional[LamTerm]:
    """Perform one outermost-leftmost beta reduction step. Returns None if no redex."""
    if isinstance(term, LamApp):
        if isinstance(term.func, LamAbs):
            # Redex found: (λv.body) arg → body[v := arg]
            return lam_subst(term.func.body, term.func.var, term.arg)
        # Outermost-leftmost: try func first, then arg
        reduced_func = beta_step(term.func)
        if reduced_func is not None:
            return LamApp(func=reduced_func, arg=term.arg)
        reduced_arg = beta_step(term.arg)
        if reduced_arg is not None:
            return LamApp(func=term.func, arg=reduced_arg)
    if isinstance(term, LamAbs):
        reduced_body = beta_step(term.body)
        if reduced_body is not None:
            return LamAbs(var=term.var, body=reduced_body)
    return None


def beta_reduce_sequence(term: LamTerm, max_steps: int = 32) -> list[LamTerm]:
    """Return [term, step1, step2, ...] until normal form or max_steps reached."""
    steps = [term]
    current = term
    for _ in range(max_steps):
        nxt = beta_step(current)
        if nxt is None:
            break
        steps.append(nxt)
        current = nxt
    return steps


def load_lambda_db() -> list[dict]:
    if LAMBDA_DB.exists():
        return json.loads(LAMBDA_DB.read_text())
    return []


def save_lambda_db(records: list[dict]) -> None:
    LAMBDA_DB.parent.mkdir(parents=True, exist_ok=True)
    LAMBDA_DB.write_text(json.dumps(records, indent=2))


def load_readings_db() -> list[tuple[str, LamTerm]]:
    if READINGS_DB.exists():
        data = json.loads(READINGS_DB.read_text())
        return [(r["name"], lam_from_dict(r["term"])) for r in data]
    return []


def save_readings_db(readings: list[tuple[str, LamTerm]]) -> None:
    READINGS_DB.parent.mkdir(parents=True, exist_ok=True)
    data = [{"name": name, "term": lam_to_dict(term)} for name, term in readings]
    READINGS_DB.write_text(json.dumps(data, indent=2))


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


def load_graph() -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(str(TTL_FILE), format="turtle")
    return g


def local_name(iri: str) -> str:
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1]
    return iri


def node_label(g: rdflib.Graph, node: rdflib.URIRef) -> str:
    label = g.value(node, RDFS.label)
    if label:
        return str(label)
    iri = str(node)
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1].replace("_", " ")
    return iri


def node_description(g: rdflib.Graph, node: rdflib.URIRef) -> str:
    desc = g.value(node, DCTERMS.description)
    return str(desc) if desc else ""


def node_types(g: rdflib.Graph, node: rdflib.URIRef) -> frozenset[str]:
    return frozenset(
        str(t).rsplit("/", 1)[-1].rsplit("#", 1)[-1]
        for t in g.objects(node, rdflib.RDF.type)
    )


def esc(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def search_nodes(g: rdflib.Graph, query: str, limit: int = 20) -> list[rdflib.URIRef]:
    query_words = query.lower().split()
    if not query_words:
        return []

    EX_NS  = "https://ahoj-db.org/kg#"
    PDB_NS = "https://www.rcsb.org/structure/"

    skip_types = (EX.Claim, PROV_ACTIVITY, OWL.ObjectProperty, OWL.DatatypeProperty)
    skip_nodes: set[rdflib.URIRef] = set()
    for typ in skip_types:
        for node in g.subjects(rdflib.RDF.type, typ):
            if isinstance(node, rdflib.URIRef):
                skip_nodes.add(node)

    candidates: set[rdflib.URIRef] = set()
    for s, _p, o in g:
        if isinstance(s, rdflib.URIRef):
            candidates.add(s)
        if isinstance(o, rdflib.URIRef):
            candidates.add(o)

    scored: list[tuple[float, rdflib.URIRef]] = []
    for node in candidates:
        if node in skip_nodes:
            continue
        iri = str(node)
        lbl = node_label(g, node).lower()
        loc = local_name(iri).replace("_", " ").lower()
        text = lbl + " " + loc
        hits = sum(1 for w in query_words if w in text)
        if hits == 0:
            continue
        boost = 2.0 if (iri.startswith(EX_NS) or iri.startswith(PDB_NS)) else 0.0
        scored.append((hits + boost, node))

    scored.sort(key=lambda x: -x[0])
    return [n for _score, n in scored[:limit]]


def _claim_predicate_label(g: rdflib.Graph, subject: rdflib.URIRef,
                           obj: rdflib.URIRef) -> str:
    """Find the rdfs:label of the direct predicate between subject and obj."""
    skip = {rdflib.RDF.type, RDFS.label, RDFS.comment,
            EX.subject, EX.object, EX.claimText, EX.citation,
            OWL.sameAs, SKOS.exactMatch}
    for pred in g.predicates(subject, obj):
        if pred in skip:
            continue
        lbl = g.value(pred, RDFS.label)
        if lbl:
            return str(lbl)
        return local_name(str(pred)).replace("_", " ")
    return "related to"


def get_claims_for_subject(g: rdflib.Graph, subject: rdflib.URIRef) -> list[dict]:
    claims = []
    for claim in g.subjects(EX.subject, subject):
        claim_text = str(g.value(claim, EX.claimText) or "")
        source_text = str(g.value(claim, EX.citation) or "")
        obj = g.value(claim, EX.object)
        obj_lbl = node_label(g, obj) if isinstance(obj, rdflib.URIRef) else str(obj or "?")
        obj_types = node_types(g, obj) if isinstance(obj, rdflib.URIRef) else frozenset()
        pred_label = (
            _claim_predicate_label(g, subject, obj)
            if isinstance(obj, rdflib.URIRef) else "?"
        )
        claims.append({
            "claim": claim,
            "claim_text": claim_text,
            "source_text": source_text,
            "object": obj,
            "predicate_label": pred_label,
            "object_label": obj_lbl,
            "object_types": obj_types,
        })
    return sorted(claims, key=lambda c: c["predicate_label"])


def get_claims_for_object(g: rdflib.Graph, object_node: rdflib.URIRef) -> list[dict]:
    claims = []
    for claim in g.subjects(EX.object, object_node):
        claim_text = str(g.value(claim, EX.claimText) or "")
        source_text = str(g.value(claim, EX.citation) or "")
        subj = g.value(claim, EX.subject)
        subj_lbl = node_label(g, subj) if isinstance(subj, rdflib.URIRef) else str(subj or "?")
        subj_types = node_types(g, subj) if isinstance(subj, rdflib.URIRef) else frozenset()
        pred_label = (
            _claim_predicate_label(g, subj, object_node)
            if isinstance(subj, rdflib.URIRef) else "?"
        )
        claims.append({
            "claim": claim,
            "claim_text": claim_text,
            "source_text": source_text,
            "subject": subj,
            "predicate_label": pred_label,
            "subject_label": subj_lbl,
            "subject_types": subj_types,
        })
    return sorted(claims, key=lambda c: c["predicate_label"])


def get_mappings_for_subject(g: rdflib.Graph, subject: rdflib.URIRef) -> list[dict]:
    mappings = []
    for pred_uri, pred_label in ((OWL.sameAs, "sameAs"), (SKOS.exactMatch, "exactMatch")):
        for obj in g.objects(subject, pred_uri):
            if not isinstance(obj, rdflib.URIRef):
                continue
            obj_lbl = node_label(g, obj)
            obj_types = node_types(g, obj)
            mappings.append({
                "mapping": obj,
                "object": obj,
                "predicate_label": pred_label,
                "object_label": obj_lbl,
                "object_types": obj_types,
                "is_exact": True,
            })
    return sorted(mappings, key=lambda m: m["predicate_label"])


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


# ── Widgets ───────────────────────────────────────────────────────────────────

class NodeItem(ListItem):
    def __init__(self, node: rdflib.URIRef, label: str, description: str = "") -> None:
        if description:
            content = f"{esc(label)}\n[dim]{esc(description)}[/dim]"
        else:
            content = esc(label)
        super().__init__(Label(content, markup=True))
        self.node = node


MODAL_CSS = """
Screen {
    align: center middle;
}
#modal-container {
    width: 80%;
    max-height: 70%;
    background: $surface;
    border: thick $accent;
    padding: 1 2;
}
#modal-title {
    text-style: bold;
    color: $accent;
    padding-bottom: 1;
}
#modal-body {
    height: 1fr;
}
"""


class ClaimTextModal(ModalScreen):
    CSS = MODAL_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("right", "to_source", "Citation"),
        Binding("left", "dismiss", "Back to claims"),
        Binding("c", "copy", "Copy"),
    ]

    def __init__(self, claim_text: str, source_text: str) -> None:
        super().__init__()
        self._claim_text = claim_text
        self._source_text = source_text

    def compose(self) -> ComposeResult:
        hint = "→ citation  |  ← back  |  c copy  |  Esc close"
        with ScrollableContainer(id="modal-container"):
            yield Static(f"Claim Text  [dim]({hint})[/dim]", id="modal-title", markup=True)
            yield Static(esc(self._claim_text), id="modal-body")

    def action_to_source(self) -> None:
        if not self._source_text:
            return
        def on_source_dismissed(close_all: bool | None) -> None:
            if close_all:
                self.dismiss()
        self.app.push_screen(SourceTextModal(self._claim_text, self._source_text), on_source_dismissed)

    def action_copy(self) -> None:
        pyperclip.copy(self._claim_text)
        self.notify("Claim text copied")


class SourceTextModal(ModalScreen):
    CSS = MODAL_CSS

    BINDINGS = [
        Binding("escape", "close_all", "Close"),
        Binding("q", "close_all", "Close"),
        Binding("left", "dismiss", "Back to claim text"),
        Binding("right", "dismiss", "Back to claim text"),
        Binding("c", "copy", "Copy"),
    ]

    def __init__(self, claim_text: str, source_text: str) -> None:
        super().__init__()
        self._source_text = source_text

    def compose(self) -> ComposeResult:
        hint = "← → back  |  c copy  |  Esc close"
        with ScrollableContainer(id="modal-container"):
            yield Static(f"Citation  [dim]({hint})[/dim]", id="modal-title", markup=True)
            yield Static(esc(self._source_text), id="modal-body")

    def action_close_all(self) -> None:
        self.dismiss(True)

    def action_copy(self) -> None:
        pyperclip.copy(self._source_text)
        self.notify("Source text copied")


class ReadingDetailModal(ModalScreen):
    CSS = MODAL_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("left", "dismiss", "Close"),
    ]

    def __init__(self, name: str, term: LamTerm, g: rdflib.Graph) -> None:
        super().__init__()
        self._name = name
        self._term = term
        self._g = g

    def compose(self) -> ComposeResult:
        type_iri = _term_type(self._term)
        type_lbl = local_name(type_iri) if type_iri else "?"
        claims = collect_edge_claims(self._g, self._term)
        claims_by_edge: dict[tuple[str, str], list[str]] = {}
        for c in claims:
            key = (c["subj_iri"], c["obj_iri"])
            claims_by_edge.setdefault(key, []).append(c["claim_text"])
        with ScrollableContainer(id="modal-container"):
            yield Static(
                f"Reading: [b]{esc(self._name)}[/b]  [dim](type: {esc(type_lbl)}  |  Esc close)[/dim]",
                id="modal-title", markup=True,
            )
            lines: list[str] = []
            _render_lam_root(self._term, claims_by_edge, lines)
            yield Static("\n".join(lines), id="modal-body", markup=True)


LAMBDA_MODAL_CSS = """
Screen {
    align: center middle;
}
#lambda-container {
    width: 80%;
    max-height: 70%;
    background: $surface;
    border: thick $warning;
    padding: 1 2;
}
#lambda-title {
    text-style: bold;
    color: $warning;
    padding-bottom: 1;
}
#lambda-search {
    height: 3;
    border: tall $warning;
    margin-bottom: 1;
}
#lambda-results {
    height: 1fr;
    border: tall $panel-lighten-2;
}
"""


class LambdaAbstractionModal(ModalScreen):
    CSS = LAMBDA_MODAL_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, g: rdflib.Graph, term: LamTerm) -> None:
        super().__init__()
        self._g = g
        self._term = term
        self._candidates: list[rdflib.URIRef] = []

    def compose(self) -> ComposeResult:
        hint = "type to search  |  ↑↓ navigate  |  Enter select  |  Esc cancel"
        with Vertical(id="lambda-container"):
            yield Static(
                f"Question  [dim]({hint})[/dim]\n"
                f"Select queried entity:",
                id="lambda-title", markup=True,
            )
            yield Input(placeholder="Search for a node…", id="lambda-search")
            yield ListView(id="lambda-results")

    def on_mount(self) -> None:
        self.query_one("#lambda-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        lv = self.query_one("#lambda-results", ListView)
        lv.clear()
        self._candidates = []
        if not query:
            return
        results = search_nodes(self._g, query, limit=5)
        self._candidates = results
        for node in results:
            lv.append(NodeItem(node, node_label(self._g, node)))

    def on_key(self, event: events.Key) -> None:
        if event.key != "down":
            return
        lv = self.query_one("#lambda-results", ListView)
        inp = self.query_one("#lambda-search", Input)
        if inp.has_focus and self._candidates:
            event.stop()
            event.prevent_default()
            lv.focus()
            lv.call_after_refresh(setattr, lv, "index", 0)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        lv = self.query_one("#lambda-results", ListView)
        if self._candidates:
            lv.focus()
            lv.call_after_refresh(setattr, lv, "index", 0)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, NodeItem):
            return
        chain_term = self._term
        var = LamVar(iri=str(item.node), label=node_label(self._g, item.node))
        abs_term = LamAbs(var=var, body=chain_term)
        self.dismiss(abs_term)


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
    EX  = "https://ahoj-db.org/kg#"
    PDB = "https://www.rcsb.org/structure/"
    def v(iri: str, label: str) -> LamVar:
        return LamVar(iri=iri, label=label)
    a = v(EX+"AHoJ",         "AHoJ")
    b = v(EX+"AHoJDB",       "AHoJ-DB")        # bound in λb
    c = v(EX+"BindingPocket","binding pocket")
    d = v(EX+"AHoJDB",       "AHoJ-DB")        # free; same IRI as b
    e = v(EX+"ApoForm",      "apo form")        # bound in λe
    f = v(EX+"BindingPocket","binding pocket")  # free; same IRI as c
    g = v(PDB+"1FNP",        "1FNP")
    h = v(EX+"CrypticSite",  "cryptic site")
    lam_a = LamAbs(var=a, body=LamApp(func=g, arg=LamApp(func=a, arg=f)))
    lam_b = LamAbs(var=b, body=LamApp(
        func=LamApp(func=LamApp(func=lam_a, arg=b), arg=c), arg=d))
    lam_e = LamAbs(var=e, body=LamApp(func=e, arg=h))
    return LamApp(func=lam_b, arg=lam_e)


def _make_beta_term() -> LamTerm:
    return _make_ontology_term()


ONTOLOGY_CSS = """
Screen {
    align: center middle;
}
#ont-container {
    width: 85%;
    max-height: 80%;
    background: $surface;
    border: thick $accent;
    padding: 1 2;
}
#ont-title {
    text-style: bold;
    color: $accent;
    padding-bottom: 1;
}
#ont-body {
    height: 1fr;
}
"""

BETA_CSS = """
Screen {
    align: center middle;
}
#beta-container {
    width: 85%;
    max-height: 80%;
    background: $surface;
    border: thick $warning;
    padding: 1 2;
}
#beta-title {
    text-style: bold;
    color: $warning;
    padding-bottom: 1;
}
#beta-body {
    height: 1fr;
}
"""


class BetaReductionModal(ModalScreen):
    CSS = BETA_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("right", "step_forward", "Next step"),
        Binding("left", "step_back", "Prev step"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._steps = beta_reduce_sequence(_make_beta_term())
        self._index = 0

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="beta-container"):
            yield Static("", id="beta-title", markup=True)
            yield Static("", id="beta-body", markup=True)

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        total = len(self._steps)
        step = self._index
        is_normal = step == total - 1
        status = "[green]normal form[/green]" if is_normal else f"step {step}/{total - 1}"
        hint = "→ next  |  ← back  |  Esc close"
        self.query_one("#beta-title", Static).update(
            f"β-reduction  [dim]({hint})[/dim]  {status}"
        )
        term = self._steps[self._index]
        lines: list[str] = []
        self._render_term_tree(term, lines)
        self.query_one("#beta-body", Static).update("\n".join(lines))

    def _render_term_tree(self, term: LamTerm, lines: list,
                          prefix: str = "", is_last: bool = True) -> None:
        connector = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")
        if isinstance(term, LamVar):
            lines.append(f"{prefix}{connector}[b]{esc(term.label)}[/b]")
        elif isinstance(term, LamAbs):
            lines.append(f"{prefix}{connector}[yellow]?[/yellow][b]{esc(term.var.label)}[/b]")
            self._render_term_tree(term.body, lines, child_prefix, is_last=True)
        elif isinstance(term, LamApp):
            lines.append(f"{prefix}{connector}[cyan]·[/cyan]")
            self._render_term_tree(term.func, lines, child_prefix, is_last=False)
            self._render_term_tree(term.arg,  lines, child_prefix, is_last=True)

    def action_step_forward(self) -> None:
        if self._index < len(self._steps) - 1:
            self._index += 1
            self._refresh()
        else:
            self.notify("Already at normal form", severity="warning")

    def action_step_back(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._refresh()
        else:
            self.notify("Already at initial term", severity="warning")


class OntologyModal(ModalScreen):
    CSS = ONTOLOGY_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("d", "beta_detail", "β-reduction detail"),
    ]

    def __init__(self, g: rdflib.Graph) -> None:
        super().__init__()
        self._g = g

    def compose(self) -> ComposeResult:
        hint = "d: β-reduction  |  Esc close"
        term = _make_ontology_term()
        claims = collect_edge_claims(self._g, term)
        claims_by_edge: dict[tuple[str, str], list[str]] = {}
        for c in claims:
            key = (c["subj_iri"], c["obj_iri"])
            claims_by_edge.setdefault(key, []).append(c["claim_text"])
        with ScrollableContainer(id="ont-container"):
            yield Static(
                f"Ontology term  [dim]({hint})[/dim]",
                id="ont-title", markup=True,
            )
            lines: list[str] = []
            self._render_root(term, claims_by_edge, lines)
            yield Static("\n".join(lines), id="ont-body", markup=True)

    def _render_root(self, term: LamTerm, claims_by_edge: dict, lines: list) -> None:
        _render_lam_root(term, claims_by_edge, lines)

    def action_beta_detail(self) -> None:
        self.app.push_screen(BetaReductionModal())


LAMBDA_BROWSER_CSS = """
Screen {
    align: center middle;
}
#lb-container {
    width: 90%;
    height: 80%;
    background: $surface;
    border: thick $success;
    padding: 1 2;
    layout: horizontal;
}
#lb-left {
    width: 1fr;
    layout: vertical;
    border-right: tall $panel-lighten-2;
    padding-right: 1;
}
#lb-title {
    text-style: bold;
    color: $success;
    height: 1;
    margin-bottom: 1;
}
#lb-search {
    height: 3;
    border: tall $success;
    margin-bottom: 1;
}
#lb-node-list {
    height: 1fr;
    border: tall $panel-lighten-2;
}
#lb-right {
    width: 1fr;
    layout: vertical;
    padding-left: 1;
}
#lb-right-label {
    height: 1;
    color: $success;
    text-style: bold;
    margin-bottom: 1;
}
#lb-lambda-list {
    height: 1fr;
    border: tall $panel-lighten-2;
}
"""

LAMBDA_DETAIL_CSS = """
Screen {
    align: center middle;
}
#ld-container {
    width: 85%;
    max-height: 80%;
    background: $surface;
    border: thick $success;
    padding: 1 2;
}
#ld-title {
    text-style: bold;
    color: $success;
    padding-bottom: 1;
}
#ld-body {
    height: 1fr;
}
"""


class LambdaRecordItem(ListItem):
    def __init__(self, record: dict, index: int) -> None:
        try:
            term = lam_from_dict(record["term"])
            term_str = str(term)
        except Exception:
            term_str = record.get("term_str", "?")
        display = f"[b]λ[/b]  {esc(term_str).replace(' · ', ' [yellow]·[/yellow] ')}"
        super().__init__(Label(display, markup=True))
        self.record = record
        self.record_index = index


class LambdaDetailModal(ModalScreen):
    CSS = LAMBDA_DETAIL_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("left", "dismiss", "Close"),
    ]

    def __init__(self, g: rdflib.Graph, record: dict) -> None:
        super().__init__()
        self._g = g
        self._record = record

    def compose(self) -> ComposeResult:
        chain_label = self._record.get("chain_label", "?")
        hint = "Esc / ← close"
        with ScrollableContainer(id="ld-container"):
            yield Static(
                f"λ-abstraction detail  [dim]({hint})[/dim]",
                id="ld-title", markup=True,
            )
            lines: list[str] = []
            lines.append(f"[b]Term:[/b]  {esc(chain_label)}")
            lines.append("")
            term = lam_from_dict(self._record["term"])
            # Build edge -> [claim_text, ...] index from stored claims
            claims_by_edge: dict[tuple[str, str], list[str]] = {}
            for c in self._record.get("claims", []):
                key = (c["subj_iri"], c["obj_iri"])
                claims_by_edge.setdefault(key, []).append(c["claim_text"])
            if isinstance(term, LamAbs):
                lines.append(f"[b]Bound variable:[/b]  [b]{esc(term.var.label)}[/b]")
                lines.append("[b]Body:[/b]")
                body = term.body
            else:
                body = term
            _render_lam_root(body, claims_by_edge, lines)
            yield Static("\n".join(lines), id="ld-body", markup=True)


class LambdaBrowserModal(ModalScreen):
    CSS = LAMBDA_BROWSER_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    def __init__(self, g: rdflib.Graph) -> None:
        super().__init__()
        self._g = g
        self._node_candidates: list[rdflib.URIRef] = []
        self._all_records: list[dict] = load_lambda_db()

    def compose(self) -> ComposeResult:
        with Horizontal(id="lb-container"):
            with Vertical(id="lb-left"):
                yield Static(
                    "λ-browser  [dim](search entity · ↑↓ · Enter)[/dim]",
                    id="lb-title", markup=True,
                )
                yield Input(placeholder="Search entity…", id="lb-search")
                yield ListView(id="lb-node-list")
            with Vertical(id="lb-right"):
                yield Static("Abstractions for selected entity:", id="lb-right-label")
                yield ListView(id="lb-lambda-list")

    def on_mount(self) -> None:
        self.query_one("#lb-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "lb-search":
            return
        query = event.value.strip()
        lv = self.query_one("#lb-node-list", ListView)
        lv.clear()
        self._node_candidates = []
        lv2 = self.query_one("#lb-lambda-list", ListView)
        lv2.clear()
        if not query:
            return
        results = search_nodes(self._g, query, limit=10)
        self._node_candidates = results
        for node in results:
            lv.append(NodeItem(node, node_label(self._g, node)))
        if results:
            lv.call_after_refresh(setattr, lv, "index", 0)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        lv = self.query_one("#lb-node-list", ListView)
        if self._node_candidates:
            lv.focus()
            if lv.index is None:
                lv.call_after_refresh(setattr, lv, "index", 0)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "lb-node-list":
            return
        item = event.item
        if not isinstance(item, NodeItem):
            return
        self._populate_lambda_list(str(item.node))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "lb-node-list":
            item = event.item
            if isinstance(item, NodeItem):
                self._populate_lambda_list(str(item.node))
                self.query_one("#lb-lambda-list", ListView).focus()
            return
        if event.list_view.id == "lb-lambda-list":
            item = event.item
            if isinstance(item, LambdaRecordItem):
                self.app.push_screen(LambdaDetailModal(self._g, item.record))

    def _populate_lambda_list(self, iri: str) -> None:
        lv = self.query_one("#lb-lambda-list", ListView)
        lv.clear()
        matches = [
            (i, r) for i, r in enumerate(self._all_records)
            if self._record_contains_iri(r, iri)
        ]
        label = self.query_one("#lb-right-label", Static)
        if matches:
            label.update(f"[b]{len(matches)}[/b] abstraction(s) for this entity:")
            for i, rec in matches:
                lv.append(LambdaRecordItem(rec, i))
            lv.call_after_refresh(setattr, lv, "index", 0)
        else:
            label.update("No abstractions for this entity.")

    @staticmethod
    def _record_contains_iri(record: dict, iri: str) -> bool:
        try:
            term = lam_from_dict(record["term"])
        except Exception:
            return False
        return isinstance(term, LamAbs) and term.var.iri == iri


class ClaimItem(ListItem):
    def __init__(self, claim_data: dict) -> None:
        iri = local_name(str(claim_data['claim']))
        display = f"[b]{esc(claim_data['predicate_label'])}[/b]  →  {esc(claim_data['object_label'])}  [dim]{esc(iri)}[/dim]"
        super().__init__(Label(display, markup=True))
        self.claim_data = claim_data


class ReverseClaimItem(ListItem):
    def __init__(self, claim_data: dict) -> None:
        iri = local_name(str(claim_data['claim']))
        display = (
            f"[b]{esc(claim_data['subject_label'])}[/b]"
            f"  →  {esc(claim_data['predicate_label'])}"
            f"  [dim]{esc(iri)}[/dim]"
        )
        super().__init__(Label(display, markup=True))
        self.claim_data = claim_data


class SeparatorItem(ListItem):
    def __init__(self, label: str) -> None:
        super().__init__(Label(f"[dim]─── {esc(label)} ───[/dim]", markup=True))
        self.disabled = True


class MappingItem(ListItem):
    def __init__(self, mapping_data: dict) -> None:
        iri = local_name(str(mapping_data['object']))
        display = (
            f"[b]{esc(mapping_data['predicate_label'])}[/b]"
            f"  →  {esc(mapping_data['object_label'])}"
            f"  [dim]{esc(iri)}[/dim]"
        )
        super().__init__(Label(display, markup=True))
        self.mapping_data = mapping_data


class ChainTargetItem(ListItem):
    """Represents an existing reading that can be composed with the current term."""
    def __init__(self, name: str, term: LamTerm, chain_index: int,
                 predicate_label: str = "", claim_iri: str = "",
                 reverse: bool = False) -> None:
        iri_part = f"  [dim]{esc(claim_iri)}[/dim]" if claim_iri else ""
        if reverse:
            display = (
                f"[yellow]⛓[/yellow] [b]{esc(name)}[/b]"
                f"  →  {esc(predicate_label)}{iri_part}"
            )
        else:
            display = (
                f"[b]{esc(predicate_label)}[/b]"
                f"  →  [yellow]⛓[/yellow] {esc(name)}{iri_part}"
            )
        super().__init__(Label(display, markup=True))
        self.reading_name = name
        self.reading_term = term
        self.reading_index = chain_index


class ReadingItem(ListItem):
    def __init__(self, name: str, term: LamTerm, index: int) -> None:
        type_iri = _term_type(term)
        type_lbl = local_name(type_iri) if type_iri else "?"
        display = f"[yellow]⛓[/yellow] [b]{esc(name)}[/b]  [dim]{esc(str(term))}  {esc(type_lbl)}[/dim]"
        super().__init__(Label(display, markup=True))
        self.reading_name = name
        self.reading_term = term
        self.reading_index = index


NAME_READING_CSS = """
Screen {
    align: center middle;
}
#nr-container {
    width: 60%;
    height: auto;
    background: $surface;
    border: thick $success;
    padding: 1 2;
}
#nr-title {
    text-style: bold;
    color: $success;
    padding-bottom: 1;
}
#nr-input {
    height: 3;
    border: tall $success;
}
#nr-term {
    color: $text-muted;
    padding-top: 1;
}
"""


class NameReadingModal(ModalScreen):
    CSS = NAME_READING_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, default_name: str, term_str: str = "") -> None:
        super().__init__()
        self._default_name = default_name
        self._term_str = term_str

    def compose(self) -> ComposeResult:
        with Vertical(id="nr-container"):
            yield Static(
                "Save reading  [dim](Enter to confirm · Esc to cancel)[/dim]",
                id="nr-title", markup=True,
            )
            yield Input(value=self._default_name, id="nr-input")
            if self._term_str:
                yield Static(esc(self._term_str), id="nr-term", markup=True)

    def on_mount(self) -> None:
        inp = self.query_one("#nr-input", Input)
        inp.focus()
        inp.cursor_position = len(self._default_name)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if name:
            self.dismiss(name)


# ── Widgets ───────────────────────────────────────────────────────────────────

# ── Application ───────────────────────────────────────────────────────────────

class KGBrowser(App):
    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    #title-bar {
        background: $primary;
        color: $text;
        padding: 0 1;
        height: 1;
        text-style: bold;
    }

    #search-box {
        height: 3;
        border: tall $accent;
    }

    #history-scroll {
        height: auto;
        max-height: 30vh;
        border: tall $primary;
        background: $surface;
    }

    #history-tree {
        padding: 0 1;
    }

    #main-area {
        height: 1fr;
        layout: horizontal;
    }

    #left-pane {
        width: 2fr;
        layout: vertical;
    }

    #right-pane {
        width: 1fr;
        layout: vertical;
        border: tall $primary;
        background: $surface;
    }

    #readings-label {
        background: $secondary;
        color: $text;
        padding: 0 1;
        height: 1;
        text-style: bold;
    }

    #readings-list {
        height: 1fr;
    }

    #list-label {
        background: $secondary;
        color: $text;
        padding: 0 1;
        height: 1;
        text-style: italic;
    }

    #results-list {
        height: 1fr;
        border: tall $panel-lighten-2;
    }

    #to-list-label {
        background: $secondary;
        color: $text;
        padding: 0 1;
        height: 1;
        text-style: italic;
    }

    #to-list {
        height: 1fr;
        border: tall $panel-lighten-2;
    }

    ListItem { padding: 0 1; }
    ListItem.--highlight { background: $accent 30%; }
    """

    BINDINGS = [
        Binding("ctrl+r", "reset", "New search"),
        Binding("right", "show_source", "Claim text / Reading detail", show=True),
        Binding("left", "go_back", "Back", show=True),
        Binding("c", "copy_item", "Copy IRI", show=True),
        Binding("tab", "focus_readings", "Readings panel", show=True),
        Binding("s", "save_reading", "Save reading", show=True),
        Binding("a", "lambda_abstraction", "Ask question", show=True),
        Binding("o", "ontology_view", "Ontology", show=True),
        Binding("b", "lambda_browser", "λ-browser", show=True),
        Binding("q", "app.quit", "Quit"),
    ]

    state: reactive[str] = reactive("search")

    def __init__(self) -> None:
        super().__init__()
        self.g = load_graph()
        self.current_node: Optional[rdflib.URIRef] = None
        self._nav_stack: list[rdflib.URIRef] = []
        self.readings: list[tuple[str, LamTerm]] = load_readings_db()
        self._current_term: Optional[LamTerm] = None    # term being built; None = bare current_node
        self._term_history: list[tuple[Optional[LamTerm], rdflib.URIRef]] = []

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            "AHoJ Knowledge Graph Browser  |  Ctrl+R: new search  |  Tab: readings panel  |  Q: quit",
            id="title-bar",
        )
        yield Input(
            placeholder="Search for a node (e.g. 'protein', 'AHoJ', 'glycogen')…",
            id="search-box",
        )
        with VerticalScroll(id="history-scroll"):
            yield Static("", id="history-tree", markup=True)
        with Horizontal(id="main-area"):
            with Vertical(id="left-pane"):
                yield Static("Type a query and press Enter to search.", id="list-label")
                yield ListView(id="results-list")
                yield Static("Claims to…", id="to-list-label")
                yield ListView(id="to-list")
            with Vertical(id="right-pane"):
                yield Static("Readings", id="readings-label")
                yield ListView(id="readings-list")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_readings_panel()
        self._show_search()

    # ── Search panel visibility ───────────────────────────────────────────────

    def _show_search(self) -> None:
        inp = self.query_one("#search-box", Input)
        inp.display = True
        inp.disabled = False
        inp.focus()
        self.query_one("#history-scroll").display = False
        self.query_one("#to-list-label").display = False
        self.query_one("#to-list").display = False

    def _hide_search(self) -> None:
        inp = self.query_one("#search-box", Input)
        inp.display = False
        inp.disabled = True
        self.query_one("#history-scroll").display = True
        self.query_one("#to-list-label").display = True
        self.query_one("#to-list").display = True

    # ── Helper: current term ──────────────────────────────────────────────────

    def _current_term_as_lam(self) -> LamTerm:
        """Return _current_term if set, else LamVar for the current node."""
        if self._current_term is not None:
            return self._current_term
        return LamVar(
            iri=str(self.current_node),
            label=node_label(self.g, self.current_node),
        )

    # ── History tree ──────────────────────────────────────────────────────────

    def _refresh_history_tree(self) -> None:
        widget = self.query_one("#history-tree", Static)
        if not self.current_node:
            widget.update("")
            return
        if self._current_term is None:
            lbl = node_label(self.g, self.current_node)
            widget.update(f"[b]{esc(lbl)}[/b]")
            return
        term = self._current_term
        claims = collect_edge_claims(self.g, term)
        claims_by_edge: dict[tuple[str, str], list[str]] = {}
        for c in claims:
            key = (c["subj_iri"], c["obj_iri"])
            claims_by_edge.setdefault(key, []).append(c["claim_text"])
        lines: list[str] = []
        _render_lam_root(term, claims_by_edge, lines)
        widget.update("\n".join(lines))
        self.query_one("#history-scroll", VerticalScroll).scroll_end(animate=False)

    # ── List label helper ─────────────────────────────────────────────────────

    def _list_label(self, markup: str) -> None:
        self.query_one("#list-label", Static).update(markup)

    # ── Readings panel helpers ────────────────────────────────────────────────

    def _refresh_readings_panel(self) -> None:
        lv = self.query_one("#readings-list", ListView)
        prev_index = lv.index
        lv.clear()
        for i, (name, term) in enumerate(self.readings):
            lv.append(ReadingItem(name, term, i))
        if self.readings:
            target = min(prev_index or 0, len(self.readings) - 1)
            lv.call_after_refresh(setattr, lv, "index", target)

    def _readings_matching_reverse(self, node: rdflib.URIRef) -> list[tuple[int, str, LamTerm, str, str]]:
        """Return (index, name, term, predicate_label, claim_iri) for readings r where claim (type(r), node) exists."""
        subj_to_claim: dict[str, tuple[str, str]] = {}
        for cd in get_claims_for_object(self.g, node):
            subj = cd["subject"]
            if isinstance(subj, rdflib.URIRef):
                subj_to_claim.setdefault(str(subj), (
                    cd["predicate_label"],
                    local_name(str(cd["claim"])),
                ))
        if not subj_to_claim:
            return []
        result = []
        for i, (name, term) in enumerate(self.readings):
            type_iri = _term_type(term)
            if type_iri and type_iri in subj_to_claim:
                pred, ciri = subj_to_claim[type_iri]
                result.append((i, name, term, pred, ciri))
        return result

    def _readings_matching_any(self, node: rdflib.URIRef) -> list[tuple[int, str, LamTerm, str, str]]:
        """Return (index, name, term, predicate_label, claim_iri) for readings r where claim (node, type(r)) exists."""
        obj_to_claim: dict[str, tuple[str, str]] = {}
        for cd in get_claims_for_subject(self.g, node):
            obj = cd["object"]
            if isinstance(obj, rdflib.URIRef):
                obj_to_claim.setdefault(str(obj), (
                    cd["predicate_label"],
                    local_name(str(cd["claim"])),
                ))
        if not obj_to_claim:
            return []
        result = []
        for i, (name, term) in enumerate(self.readings):
            type_iri = _term_type(term)
            if type_iri and type_iri in obj_to_claim:
                pred, ciri = obj_to_claim[type_iri]
                result.append((i, name, term, pred, ciri))
        return result

    # ── Node display ──────────────────────────────────────────────────────────

    def _show_node_and_claims(self, node: rdflib.URIRef, push_stack: bool = True) -> None:
        if push_stack:
            self._nav_stack.append(node)
        self.current_node = node
        lbl = node_label(self.g, node)
        self._refresh_history_tree()

        claims = get_claims_for_subject(self.g, node)
        mappings = get_mappings_for_subject(self.g, node)
        reverse_claims = get_claims_for_object(self.g, node)
        lv = self.query_one("#results-list", ListView)
        lv.clear()
        if not claims and not mappings:
            self._list_label(
                f"No outgoing claims for [b]{esc(lbl)}[/b].  Ctrl+R to search again."
            )
        else:
            matching = self._readings_matching_any(node)
            if matching:
                self._list_label(
                    f"Claims from [b]{esc(lbl)}[/b] — Enter: follow  |  s: save reading  |  select ⛓ to compose:"
                )
            else:
                self._list_label(
                    f"Claims from [b]{esc(lbl)}[/b] — Enter: follow  |  s: save reading:"
                )
            for cd in claims:
                lv.append(ClaimItem(cd))
            if mappings:
                lv.append(SeparatorItem("External Mappings"))
                for md in mappings:
                    lv.append(MappingItem(md))
            if matching:
                lv.append(SeparatorItem("Compose with saved reading"))
                for idx, name, term, pred, ciri in matching:
                    lv.append(ChainTargetItem(name, term, idx, pred, ciri))
            lv.focus()
            lv.call_after_refresh(setattr, lv, "index", 0)

        to_lv = self.query_one("#to-list", ListView)
        to_lv.clear()
        to_label = self.query_one("#to-list-label", Static)
        matching_rev = self._readings_matching_reverse(node)
        if reverse_claims or matching_rev:
            hint = "s: save reading"
            if reverse_claims:
                hint = "Enter: follow  |  " + hint
            if matching_rev:
                hint += "  |  select ⛓ to compose"
            to_label.update(f"Claims to [b]{esc(lbl)}[/b] — {hint}:")
            for rc in reverse_claims:
                to_lv.append(ReverseClaimItem(rc))
            if matching_rev:
                to_lv.append(SeparatorItem("Compose with saved reading"))
                for idx, name, term, pred, ciri in matching_rev:
                    to_lv.append(ChainTargetItem(name, term, idx, pred, ciri, reverse=True))
            to_lv.call_after_refresh(setattr, to_lv, "index", 0)
        else:
            to_label.update(f"Claims to [b]{esc(lbl)}[/b]:")

        self.state = "claims_list"

    # ── Term snapshot ─────────────────────────────────────────────────────────

    def _push_term_snapshot(self) -> None:
        self._term_history.append((self._current_term, self.current_node))

    # ── Events ────────────────────────────────────────────────────────────────

    def _run_search(self, query: str) -> None:
        results = search_nodes(self.g, query)
        matching_readings = [
            (name, term) for name, term in self.readings
            if query.lower() in name.lower()
        ]
        lv = self.query_one("#results-list", ListView)
        lv.clear()
        if not results and not matching_readings:
            self._list_label(f"No nodes found for [i]{esc(query)}[/i].")
            self.state = "search"
            return
        self._list_label(
            f"Results for [i]{esc(query)}[/i] — select a node (↑↓ + Enter):"
        )
        if results:
            lv.append(SeparatorItem("Entities"))
            for node in results:
                lv.append(NodeItem(node, node_label(self.g, node), node_description(self.g, node)))
        if matching_readings:
            lv.append(SeparatorItem("Readings"))
            for i, (name, term) in enumerate(matching_readings):
                idx = next(j for j, (n, _) in enumerate(self.readings) if n == name)
                lv.append(ReadingItem(name, term, idx))
        self.state = "node_list"
        lv.call_after_refresh(setattr, lv, "index", 1)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-box":
            return
        query = event.value.strip()
        if not query:
            self.query_one("#results-list", ListView).clear()
            self._list_label("Type a query and press Enter to search.")
            self.state = "search"
            return
        self._run_search(query)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search-box":
            return
        lv = self.query_one("#results-list", ListView)
        if self.state == "node_list":
            lv.focus()
            if lv.index is None:
                lv.call_after_refresh(setattr, lv, "index", 1)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        lv_id = event.list_view.id

        # ── Readings panel selection ───────────────────────────────────────
        if lv_id == "readings-list" and isinstance(item, ReadingItem):
            self.push_screen(ReadingDetailModal(item.reading_name, item.reading_term, self.g))
            return

        # ── Main list selections ───────────────────────────────────────────
        if self.state == "node_list" and isinstance(item, ReadingItem):
            type_iri = _term_type(item.reading_term)
            if not type_iri:
                return
            self._current_term = item.reading_term
            self._hide_search()
            self._show_node_and_claims(rdflib.URIRef(type_iri))
            return

        if self.state == "node_list" and isinstance(item, NodeItem):
            self._hide_search()
            self._show_node_and_claims(item.node)

        elif self.state == "claims_list" and isinstance(item, ClaimItem):
            cd = item.claim_data
            next_node = cd["object"]
            if not isinstance(next_node, rdflib.URIRef):
                self._list_label("Claim object is a literal — cannot navigate. Ctrl+R to search again.")
                return
            self._push_term_snapshot()
            new_term = LamApp(self._current_term_as_lam(), LamVar(iri=str(next_node), label=cd["object_label"]))
            self._current_term = new_term
            self._show_node_and_claims(next_node)

        elif self.state == "claims_list" and isinstance(item, MappingItem):
            md = item.mapping_data
            next_node = md["object"]
            if not isinstance(next_node, rdflib.URIRef):
                self._list_label("Mapping object is a literal — cannot navigate. Ctrl+R to search again.")
                return
            self._push_term_snapshot()
            new_term = LamApp(self._current_term_as_lam(), LamVar(iri=str(next_node), label=md["object_label"]))
            self._current_term = new_term
            self._show_node_and_claims(next_node)

        elif lv_id == "to-list" and isinstance(item, ReverseClaimItem):
            cd = item.claim_data
            a_node = cd["subject"]
            if not isinstance(a_node, rdflib.URIRef):
                self._list_label("Claim subject is a literal — cannot navigate. Ctrl+R to search again.")
                return
            self._push_term_snapshot()
            stay_node = self.current_node
            a_var = LamVar(iri=str(a_node), label=cd["subject_label"])
            new_term = LamApp(a_var, self._current_term_as_lam())
            self._current_term = new_term
            self._show_node_and_claims(stay_node, push_stack=False)

        elif lv_id == "to-list" and isinstance(item, ChainTargetItem):
            self._push_term_snapshot()
            stay_node = self.current_node
            new_term = LamApp(item.reading_term, self._current_term_as_lam())
            self._current_term = new_term
            self.notify(f"Composed — reading: {item.reading_name}")
            self._show_node_and_claims(stay_node, push_stack=False)

        elif self.state == "claims_list" and isinstance(item, ChainTargetItem):
            self._push_term_snapshot()
            new_term = LamApp(self._current_term_as_lam(), item.reading_term)
            self._current_term = new_term
            self._nav_stack.clear()
            tail_iri = _term_type(item.reading_term)
            tail_node = rdflib.URIRef(tail_iri) if tail_iri else self.current_node
            type_lbl = local_name(tail_iri) if tail_iri else "?"
            self.notify(f"Composed — continuing from: {type_lbl}")
            self._show_node_and_claims(tail_node)

    # ── Key handlers ─────────────────────────────────────────────────────────

    def action_save_reading(self) -> None:
        self._handle_save_reading()

    def action_lambda_abstraction(self) -> None:
        self._handle_lambda_abstraction()

    def action_ontology_view(self) -> None:
        self.push_screen(OntologyModal(self.g))

    def action_lambda_browser(self) -> None:
        self.push_screen(LambdaBrowserModal(self.g))

    def _handle_save_reading(self) -> None:
        if self.state != "claims_list":
            return
        if self._current_term is None:
            return
        term = self._current_term
        default_name = _top_claim_name(self.g, term)
        term_str = str(term)

        def on_name_chosen(name: str | None) -> None:
            if not name:
                return
            self.readings.append((name, term))
            save_readings_db(self.readings)
            self._refresh_readings_panel()
            self.notify(f"Reading saved: {name}")
            self.call_after_refresh(self.action_reset)

        self.push_screen(NameReadingModal(default_name, term_str), on_name_chosen)

    def _handle_lambda_abstraction(self) -> None:
        if self.state != "claims_list" or self._current_term is None:
            self.notify("Start building a chain first", severity="warning")
            return
        term = self._current_term_as_lam()

        def on_lambda_selected(abs_term: LamAbs | None) -> None:
            if abs_term is None:
                return
            term_name = _top_claim_name(self.g, abs_term.body) if isinstance(abs_term, LamAbs) else str(abs_term)
            append_lambda_term(term_name, abs_term, self.g)
            self.notify(f"Saved: {abs_term}", timeout=6)
            self.call_after_refresh(self.action_reset)

        self.push_screen(LambdaAbstractionModal(self.g, term), on_lambda_selected)

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_focus_readings(self) -> None:
        lv = self.query_one("#readings-list", ListView)
        lv.focus()
        if self.readings and lv.index is None:
            lv.call_after_refresh(setattr, lv, "index", 0)

    def on_key(self, event: events.Key) -> None:
        if len(self.screen_stack) > 1:
            return
        search_box = self.query_one("#search-box", Input)
        if search_box.has_focus and event.key == "down" and self.state == "node_list":
            event.stop()
            event.prevent_default()
            lv = self.query_one("#results-list", ListView)
            lv.focus()
            lv.call_after_refresh(setattr, lv, "index", 1)
            return

    def action_go_back(self) -> None:
        if self.state != "claims_list" or not self._term_history:
            return
        term, node = self._term_history.pop()
        self._current_term = term
        if node in self._nav_stack:
            idx = len(self._nav_stack) - 1 - self._nav_stack[::-1].index(node)
            self._nav_stack = self._nav_stack[:idx + 1]
        self._show_node_and_claims(node, push_stack=False)

    def action_copy_item(self) -> None:
        lv = self.query_one("#results-list", ListView)
        item = lv.highlighted_child
        if isinstance(item, ClaimItem):
            pyperclip.copy(str(item.claim_data["claim"]))
            self.notify("Claim IRI copied")
        elif isinstance(item, MappingItem):
            pyperclip.copy(str(item.mapping_data["object"]))
            self.notify("Mapping object IRI copied")
        elif isinstance(item, NodeItem):
            pyperclip.copy(str(item.node))
            self.notify("Node IRI copied")

    def action_show_source(self) -> None:
        # Readings panel focused → show reading detail
        readings_lv = self.query_one("#readings-list", ListView)
        if readings_lv.has_focus:
            item = readings_lv.highlighted_child
            if isinstance(item, ReadingItem):
                self.push_screen(ReadingDetailModal(item.reading_name, item.reading_term, self.g))
            return

        if self.state != "claims_list":
            return

        # Determine which list is focused
        to_lv = self.query_one("#to-list", ListView)
        results_lv = self.query_one("#results-list", ListView)
        if to_lv.has_focus:
            item = to_lv.highlighted_child
        elif results_lv.has_focus:
            item = results_lv.highlighted_child
        else:
            return

        if isinstance(item, (ClaimItem, MappingItem)):
            cd = item.claim_data if isinstance(item, ClaimItem) else item.mapping_data
            claim_text = cd.get("claim_text", "")
            source_text = cd.get("source_text", "")
            if claim_text:
                self.call_after_refresh(self.push_screen, ClaimTextModal(claim_text, source_text))
        elif isinstance(item, ReverseClaimItem):
            claim_text = item.claim_data.get("claim_text", "")
            source_text = item.claim_data.get("source_text", "")
            if claim_text:
                self.call_after_refresh(self.push_screen, ClaimTextModal(claim_text, source_text))
        elif isinstance(item, ChainTargetItem):
            self.call_after_refresh(self.push_screen, ReadingDetailModal(item.reading_name, item.reading_term, self.g))
        elif isinstance(item, ReadingItem):
            self.call_after_refresh(self.push_screen, ReadingDetailModal(item.reading_name, item.reading_term, self.g))

    def action_reset(self) -> None:
        self.current_node = None
        self.state = "search"
        self._nav_stack.clear()
        self._current_term = None
        self._term_history.clear()
        self._refresh_history_tree()
        self._list_label("Type a query and press Enter to search.")
        self.query_one("#results-list", ListView).clear()
        self.query_one("#to-list", ListView).clear()
        self.query_one("#to-list-label", Static).update("Claims to…")
        self.query_one("#search-box", Input).value = ""
        self._show_search()


if __name__ == "__main__":
    app = KGBrowser()
    app.run(mouse=False)
