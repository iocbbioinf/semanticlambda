#!/usr/bin/env python3
"""Knowledge Graph Browser — terminal app for exploring the AHoJ RDF knowledge graph."""

from __future__ import annotations

import json
import shutil
import textwrap
from dataclasses import dataclass, field, replace as dc_replace
from pathlib import Path
from typing import Optional, Union

import pyperclip
import rdflib
from rdflib.namespace import RDFS, OWL, SKOS
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static
from textual.containers import VerticalScroll, ScrollableContainer, Horizontal, Vertical

TTL_FILE = Path("/home/marek/uochb/ch/paperskg/ahoj-db.ttl")
LAMBDA_DB = Path(__file__).parent / "data" / "lambda_terms.json"

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


def chain_to_lam(chain: "Chain") -> LamTerm:
    """Convert a chain's binary tree into a lambda application term."""
    def step_to_lam(step: "ChainStep") -> LamTerm:
        subj = LamVar(iri=str(step.subject), label=step.subject_label)
        obj = LamVar(iri=str(step.object_node), label=step.object_label)
        if step.sub_chain:
            sub_term = chain_to_lam(step.sub_chain)
            func = LamApp(func=LamApp(func=subj, arg=sub_term), arg=obj)
        else:
            func = LamApp(func=subj, arg=obj)
        return func

    if not chain.steps:
        raise ValueError("Cannot convert empty chain to lambda term")

    # Build left-associative application across steps
    term: LamTerm = step_to_lam(chain.steps[0])
    for step in chain.steps[1:]:
        obj = LamVar(iri=str(step.object_node), label=step.object_label)
        term = LamApp(func=term, arg=obj)
    return term


def load_lambda_db() -> list[dict]:
    if LAMBDA_DB.exists():
        return json.loads(LAMBDA_DB.read_text())
    return []


def save_lambda_db(records: list[dict]) -> None:
    LAMBDA_DB.parent.mkdir(parents=True, exist_ok=True)
    LAMBDA_DB.write_text(json.dumps(records, indent=2))


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
    result: list[dict] = []
    for subj_iri, obj_iri in collect_app_edges(term):
        subj = rdflib.URIRef(subj_iri)
        obj  = rdflib.URIRef(obj_iri)
        for claim in g.subjects(EX.subject, subj):
            if g.value(claim, EX.object) != obj:
                continue
            text = str(g.value(claim, EX.claimText) or "")
            result.append({
                "subj_iri": subj_iri,
                "obj_iri": obj_iri,
                "claim_iri": str(claim),
                "claim_text": text,
            })
    return result


def append_lambda_term(chain_label: str, term: LamTerm, g: rdflib.Graph) -> None:
    records = load_lambda_db()
    records.append({
        "chain_label": chain_label,
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

    skip_types = (EX.Claim, PROV_ACTIVITY, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.Class)
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


# ── Chain data model ──────────────────────────────────────────────────────────

@dataclass
class ChainStep:
    subject: rdflib.URIRef
    subject_label: str
    predicate_label: str
    object_node: rdflib.URIRef
    object_label: str
    kind: str  # "claim" or "mapping"
    claim_iri: str = ""
    claim_text: str = ""
    object_types: frozenset = field(default_factory=frozenset)
    # sub-chain attached at this node (composited saved chain)
    sub_chain: Optional["Chain"] = None


@dataclass
class Chain:
    steps: list[ChainStep] = field(default_factory=list)

    @property
    def tail_types(self) -> frozenset[str]:
        return self.steps[-1].object_types if self.steps else frozenset()

    @property
    def label(self) -> str:
        return _chain_label(self)

    @property
    def type_label(self) -> str:
        types = self.steps[-1].object_types if self.steps else frozenset()
        return ", ".join(sorted(types)) if types else "?"


def _chain_label(chain: "Chain") -> str:
    if not chain.steps:
        return "(empty)"
    parts = []
    for i, step in enumerate(chain.steps):
        if i == 0:
            parts.append(step.subject_label)
        if step.sub_chain:
            parts.append(f"({_chain_label(step.sub_chain)})")
        parts.append(step.object_label)
    return " → ".join(parts)


# ── Widgets ───────────────────────────────────────────────────────────────────

class NodeItem(ListItem):
    def __init__(self, node: rdflib.URIRef, label: str) -> None:
        super().__init__(Label(esc(label)))
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
        self.app.push_screen(SourceTextModal(self._claim_text, self._source_text))

    def action_copy(self) -> None:
        pyperclip.copy(self._claim_text)
        self.notify("Claim text copied")


class SourceTextModal(ModalScreen):
    CSS = MODAL_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("left", "dismiss", "Back to claim text"),
        Binding("c", "copy", "Copy"),
    ]

    def __init__(self, claim_text: str, source_text: str) -> None:
        super().__init__()
        self._source_text = source_text

    def compose(self) -> ComposeResult:
        hint = "← back  |  c copy  |  Esc close"
        with ScrollableContainer(id="modal-container"):
            yield Static(f"Citation  [dim]({hint})[/dim]", id="modal-title", markup=True)
            yield Static(esc(self._source_text), id="modal-body")

    def action_copy(self) -> None:
        pyperclip.copy(self._source_text)
        self.notify("Source text copied")


def _render_chain(chain: Chain, lines: list, indent: int) -> None:
    pad = "  " * indent
    for i, step in enumerate(chain.steps):
        kind_tag = "[dim]claim[/dim]" if step.kind == "claim" else "[dim]exactMatch[/dim]"
        # Show subject only for first step (subsequent steps share subject with prev object)
        if i == 0:
            lines.append(f"{pad}[b]{esc(step.subject_label)}[/b]")
        iri_tag = f"  [dim]{esc(step.claim_iri)}[/dim]" if step.claim_iri else ""
        if step.sub_chain:
            lines.append(f"{pad}  [yellow]╠═ branch:[/yellow]")
            _render_chain(step.sub_chain, lines, indent + 2)
            lines.append(f"{pad}  [yellow]╚═[/yellow]  [i]{esc(step.predicate_label)}[/i]  →  [b]{esc(step.object_label)}[/b]  {kind_tag}{iri_tag}")
        else:
            lines.append(f"{pad}  [i]{esc(step.predicate_label)}[/i]  →  [b]{esc(step.object_label)}[/b]  {kind_tag}{iri_tag}")
        if step.claim_text:
            lines.append(f"{pad}    [dim]{esc(step.claim_text)}[/dim]")
        if i + 1 < len(chain.steps):
            lines.append(f"{pad}[b]{esc(step.object_label)}[/b]")


class ChainDetailModal(ModalScreen):
    CSS = MODAL_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("left", "dismiss", "Close"),
    ]

    def __init__(self, chain: Chain, g: rdflib.Graph) -> None:
        super().__init__()
        self._chain = chain
        self._g = g

    def compose(self) -> ComposeResult:
        term = chain_to_lam(self._chain)
        claims = collect_edge_claims(self._g, term)
        claims_by_edge: dict[tuple[str, str], list[str]] = {}
        for c in claims:
            key = (c["subj_iri"], c["obj_iri"])
            claims_by_edge.setdefault(key, []).append(c["claim_text"])
        with ScrollableContainer(id="modal-container"):
            yield Static(
                f"Term: [b]{esc(self._chain.label)}[/b]  [dim](type: {esc(self._chain.type_label)}  |  Esc close)[/dim]",
                id="modal-title", markup=True,
            )
            lines: list[str] = []
            _render_lam_root(term, claims_by_edge, lines)
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

    def __init__(self, g: rdflib.Graph, chain: "Chain") -> None:
        super().__init__()
        self._g = g
        self._chain = chain
        self._candidates: list[rdflib.URIRef] = []

    def compose(self) -> ComposeResult:
        chain_term = chain_to_lam(self._chain)
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
        if results:
            lv.call_after_refresh(setattr, lv, "index", 0)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        lv = self.query_one("#lambda-results", ListView)
        if self._candidates:
            # focus the list so user can pick with arrows / Enter
            lv.focus()
            if lv.index is None:
                lv.call_after_refresh(setattr, lv, "index", 0)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, NodeItem):
            return
        chain_term = chain_to_lam(self._chain)
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
        display = f"[b]{esc(claim_data['subject_label'])}[/b]  →  [dim]{esc(iri)}[/dim]"
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
    """Represents an existing chain that can receive the current step."""
    def __init__(self, chain: Chain, chain_index: int) -> None:
        display = (
            f"[yellow]⛓[/yellow]  [b]{esc(chain.label)}[/b]"
            f"  [dim]({esc(chain.type_label)})[/dim]"
        )
        super().__init__(Label(display, markup=True))
        self.chain = chain
        self.chain_index = chain_index


class ChainListItem(ListItem):
    """Represents a chain in the chains panel."""
    def __init__(self, chain: Chain, chain_index: int) -> None:
        display = (
            f"[yellow]⛓[/yellow] [b]{esc(chain.label)}[/b]"
            f"  [dim]{esc(chain.type_label)}[/dim]"
        )
        super().__init__(Label(display, markup=True))
        self.chain = chain
        self.chain_index = chain_index


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

    #chains-label {
        background: $secondary;
        color: $text;
        padding: 0 1;
        height: 1;
        text-style: bold;
    }

    #chains-list {
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
        Binding("right", "show_source", "Claim text / Chain detail", show=True),
        Binding("left", "go_back", "Back", show=True),
        Binding("c", "copy_item", "Copy IRI", show=True),
        Binding("tab", "focus_chains", "Terms panel", show=True),
        Binding("s", "save_chain", "Save term", show=True),
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
        self.chains: list[Chain] = []
        self._current_steps: list[ChainStep] = []  # steps taken in current navigation path
        self._pending_step: Optional[ChainStep] = None
        self._chain_prefix: Optional[Chain] = None  # chain selected before any steps taken
        self._chain_prefix_subject: Optional[rdflib.URIRef] = None  # node where prefix was selected

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            "AHoJ Knowledge Graph Browser  |  Ctrl+R: new search  |  Tab: chains panel  |  Q: quit",
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
                yield Static("Terms", id="chains-label")
                yield ListView(id="chains-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#search-box", Input).focus()

    # ── History tree ──────────────────────────────────────────────────────────

    def _refresh_history_tree(self) -> None:
        widget = self.query_one("#history-tree", Static)
        steps = list(self._current_steps)
        if not steps and self._chain_prefix is None:
            if self.current_node:
                lbl = node_label(self.g, self.current_node)
                widget.update(f"[b]{esc(lbl)}[/b]")
            else:
                widget.update("")
            return
        if self._chain_prefix is not None:
            # Build c · prefix_term [· step1_obj · …] directly
            c_var = LamVar(
                iri=str(self._chain_prefix_subject),
                label=node_label(self.g, self._chain_prefix_subject),
            )
            term: LamTerm = LamApp(func=c_var, arg=chain_to_lam(self._chain_prefix))
            for step in steps:
                term = LamApp(func=term, arg=LamVar(iri=str(step.object_node), label=step.object_label))
        else:
            term = chain_to_lam(Chain(steps=steps))
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

    # ── Chains panel helpers ──────────────────────────────────────────────────

    def _refresh_chains_panel(self) -> None:
        lv = self.query_one("#chains-list", ListView)
        prev_index = lv.index
        lv.clear()
        for i, chain in enumerate(self.chains):
            lv.append(ChainListItem(chain, i))
        if self.chains:
            target = min(prev_index or 0, len(self.chains) - 1)
            lv.call_after_refresh(setattr, lv, "index", target)

    def _chains_matching_any(self, claims: list[dict], mappings: list[dict]) -> list[tuple[int, Chain]]:
        """Return saved chains whose tail node is the object of some claim/exactMatch in the current list."""
        obj_nodes: set[rdflib.URIRef] = set()
        for cd in claims:
            if isinstance(cd["object"], rdflib.URIRef):
                obj_nodes.add(cd["object"])
        for md in mappings:
            if md.get("is_exact") and isinstance(md["object"], rdflib.URIRef):
                obj_nodes.add(md["object"])
        if not obj_nodes:
            return []
        return [
            (i, c) for i, c in enumerate(self.chains)
            if c.steps and c.steps[-1].object_node in obj_nodes
        ]

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
            matching = self._chains_matching_any(claims, mappings)
            if matching:
                self._list_label(
                    f"Claims from [b]{esc(lbl)}[/b] — Enter: follow  |  s: save term  |  select ⛓ to compose:"
                )
            else:
                self._list_label(
                    f"Claims from [b]{esc(lbl)}[/b] — Enter: follow  |  s: save term:"
                )
            for cd in claims:
                lv.append(ClaimItem(cd))
            if mappings:
                lv.append(SeparatorItem("External Mappings"))
                for md in mappings:
                    lv.append(MappingItem(md))
            if matching:
                lv.append(SeparatorItem("Compose with saved term"))
                for idx, chain in matching:
                    lv.append(ChainTargetItem(chain, idx))
            lv.focus()
            lv.call_after_refresh(setattr, lv, "index", 0)

        to_lv = self.query_one("#to-list", ListView)
        to_lv.clear()
        to_label = self.query_one("#to-list-label", Static)
        if reverse_claims:
            to_label.update(f"Claims to [b]{esc(lbl)}[/b] — Enter: follow:")
            for rc in reverse_claims:
                to_lv.append(ReverseClaimItem(rc))
        else:
            to_label.update(f"Claims to [b]{esc(lbl)}[/b]:")

        self.state = "claims_list"

    # ── Step helpers ──────────────────────────────────────────────────────────

    def _make_claim_step(self, cd: dict) -> ChainStep:
        return ChainStep(
            subject=self.current_node,
            subject_label=node_label(self.g, self.current_node),
            predicate_label=cd["predicate_label"],
            object_node=cd["object"],
            object_label=cd["object_label"],
            kind="claim",
            claim_iri=local_name(str(cd["claim"])),
            claim_text=cd.get("claim_text", ""),
            object_types=cd["object_types"],
        )

    def _make_mapping_step(self, md: dict) -> ChainStep:
        return ChainStep(
            subject=self.current_node,
            subject_label=node_label(self.g, self.current_node),
            predicate_label=md["predicate_label"],
            object_node=md["object"],
            object_label=md["object_label"],
            kind="mapping",
            claim_iri=local_name(str(md["object"])),
            object_types=md["object_types"],
        )


    # ── Events ────────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        results = search_nodes(self.g, query)
        lv = self.query_one("#results-list", ListView)
        lv.clear()
        if not results:
            self._list_label(f"No nodes found for [i]{esc(query)}[/i].")
            return
        self._list_label(
            f"Results for [i]{esc(query)}[/i] — select a node (↑↓ + Enter):"
        )
        for node in results:
            lv.append(NodeItem(node, node_label(self.g, node)))
        self.state = "node_list"
        lv.focus()
        lv.call_after_refresh(setattr, lv, "index", 0)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        lv_id = event.list_view.id

        # ── Chains panel selection ─────────────────────────────────────────
        if lv_id == "chains-list" and isinstance(item, ChainListItem):
            self.push_screen(ChainDetailModal(item.chain, self.g))
            return

        # ── Main list selections ───────────────────────────────────────────
        if self.state == "node_list" and isinstance(item, NodeItem):
            self._show_node_and_claims(item.node)

        elif self.state == "claims_list" and isinstance(item, ClaimItem):
            cd = item.claim_data
            next_node = cd["object"]
            if not isinstance(next_node, rdflib.URIRef):
                self._list_label("Claim object is a literal — cannot navigate. Ctrl+R to search again.")
                return
            step = self._make_claim_step(cd)
            self._current_steps.append(step)
            self._show_node_and_claims(next_node)

        elif self.state == "claims_list" and isinstance(item, MappingItem):
            md = item.mapping_data
            next_node = md["object"]
            if not isinstance(next_node, rdflib.URIRef):
                self._list_label("Mapping object is a literal — cannot navigate. Ctrl+R to search again.")
                return
            step = self._make_mapping_step(md)
            self._current_steps.append(step)
            self._show_node_and_claims(next_node)

        elif lv_id == "to-list" and isinstance(item, ReverseClaimItem):
            cd = item.claim_data
            a_node = cd["subject"]
            if not isinstance(a_node, rdflib.URIRef):
                self._list_label("Claim subject is a literal — cannot navigate. Ctrl+R to search again.")
                return
            # Prepend a: new term = a · t, type(a · t) = type(t) = T (retained)
            # Stay at current node T — claims from/to T remain correct.
            stay_node = self.current_node
            steps = list(self._current_steps)
            if not steps and self._chain_prefix is None:
                # Current term is a bare variable; build a · current_node as one ChainStep
                self._current_steps = [ChainStep(
                    subject=a_node,
                    subject_label=cd["subject_label"],
                    predicate_label=cd["predicate_label"],
                    object_node=self.current_node,
                    object_label=node_label(self.g, self.current_node),
                    kind="claim",
                    claim_iri=local_name(str(cd["claim"])),
                    claim_text=cd.get("claim_text", ""),
                    object_types=node_types(self.g, self.current_node),
                )]
            else:
                # Fold current state into _chain_prefix; a becomes the new head
                if self._chain_prefix is not None:
                    subj = self._chain_prefix_subject or (steps[0].subject if steps else None)
                    subj_label = node_label(self.g, subj) if subj else ""
                    if steps:
                        steps[0] = dc_replace(steps[0], sub_chain=self._chain_prefix,
                                              subject=subj, subject_label=subj_label)
                    else:
                        tail = self._chain_prefix.steps[-1]
                        steps = [dc_replace(tail, subject=subj, subject_label=subj_label,
                                            sub_chain=self._chain_prefix)]
                self._chain_prefix = Chain(steps=steps)
                self._chain_prefix_subject = a_node
                self._current_steps.clear()
            self._show_node_and_claims(stay_node, push_stack=False)

        elif self.state == "claims_list" and isinstance(item, ChainTargetItem):
            # Attach selected chain as sub_chain of the last step (or store as prefix
            # if no steps taken yet), then continue navigation from the chain's tail.
            if self._current_steps:
                self._current_steps[-1].sub_chain = item.chain
            else:
                self._chain_prefix = item.chain
                self._chain_prefix_subject = self.current_node
            self._nav_stack.clear()
            tail_node = item.chain.steps[-1].object_node
            self.notify(f"Composed — continuing from: {item.chain.steps[-1].object_label}")
            self._show_node_and_claims(tail_node)

    # ── Key handlers ─────────────────────────────────────────────────────────

    def action_save_chain(self) -> None:
        self._handle_save_chain()

    def action_lambda_abstraction(self) -> None:
        self._handle_lambda_abstraction()

    def action_ontology_view(self) -> None:
        self.push_screen(OntologyModal(self.g))

    def action_lambda_browser(self) -> None:
        self.push_screen(LambdaBrowserModal(self.g))

    def _handle_save_chain(self) -> None:
        if self.state != "claims_list":
            return
        steps = list(self._current_steps)
        if not steps and self._chain_prefix is None:
            return
        if self._chain_prefix is not None:
            subj = self._chain_prefix_subject or (steps[0].subject if steps else None)
            subj_label = node_label(self.g, subj) if subj else ""
            if steps:
                steps[0] = dc_replace(
                    steps[0],
                    sub_chain=self._chain_prefix,
                    subject=subj,
                    subject_label=subj_label,
                )
            else:
                # prefix only (no further steps): synthesise a step from c to the
                # tail of the prefix chain so it can be stored as a Chain object
                tail = self._chain_prefix.steps[-1]
                steps = [dc_replace(
                    tail,
                    subject=subj,
                    subject_label=subj_label,
                    sub_chain=self._chain_prefix,
                )]
        chain = Chain(steps=steps)
        self.chains.append(chain)
        self._refresh_chains_panel()
        self.notify(f"Term saved: {chain.label}")
        # reset navigation to start a new path
        self._current_steps.clear()
        self._nav_stack.clear()
        self._pending_step = None
        self._chain_prefix = None
        self._chain_prefix_subject = None
        self._refresh_history_tree()
        self._list_label("Term saved — start a new search:")
        lv = self.query_one("#results-list", ListView)
        lv.clear()
        self.state = "search"
        inp = self.query_one("#search-box", Input)
        inp.value = ""
        inp.focus()

    def _handle_lambda_abstraction(self) -> None:
        if self.state != "claims_list" or not self._current_steps:
            self.notify("Start building a chain first", severity="warning")
            return
        steps = list(self._current_steps)
        if self._chain_prefix is not None:
            subj = self._chain_prefix_subject or steps[0].subject
            steps[0] = dc_replace(
                steps[0],
                sub_chain=self._chain_prefix,
                subject=subj,
                subject_label=node_label(self.g, subj),
            )
        chain = Chain(steps=steps)

        def on_lambda_selected(abs_term: LamAbs | None) -> None:
            if abs_term is None:
                return
            append_lambda_term(chain.label, abs_term, self.g)
            self.notify(f"Saved: {abs_term}", timeout=6)

        self.push_screen(LambdaAbstractionModal(self.g, chain), on_lambda_selected)

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_focus_chains(self) -> None:
        lv = self.query_one("#chains-list", ListView)
        lv.focus()
        if self.chains and lv.index is None:
            lv.call_after_refresh(setattr, lv, "index", 0)

    def on_key(self, event: events.Key) -> None:
        to_lv = self.query_one("#to-list", ListView)
        if not to_lv.has_focus:
            return
        if event.key == "left":
            event.stop()
            self.action_go_back()
        elif event.key == "right":
            event.stop()
            self.action_show_source()

    def action_go_back(self) -> None:
        if self.state != "claims_list" or len(self._nav_stack) < 2:
            return
        self._pending_step = None
        self._chain_prefix = None
        self._chain_prefix_subject = None
        if self._current_steps:
            self._current_steps.pop()
        self._nav_stack.pop()
        prev_node = self._nav_stack[-1]
        self._show_node_and_claims(prev_node, push_stack=False)

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
        # If chains panel is focused, show chain detail
        chains_lv = self.query_one("#chains-list", ListView)
        if chains_lv.has_focus:
            item = chains_lv.highlighted_child
            if isinstance(item, ChainListItem):
                self.push_screen(ChainDetailModal(item.chain, self.g))
            return

        if self.state != "claims_list":
            return
        to_lv = self.query_one("#to-list", ListView)
        if to_lv.has_focus:
            item = to_lv.highlighted_child
            if isinstance(item, ReverseClaimItem):
                claim_text = item.claim_data.get("claim_text", "")
                source_text = item.claim_data.get("source_text", "")
                if claim_text:
                    self.push_screen(ClaimTextModal(claim_text, source_text))
            return
        lv = self.query_one("#results-list", ListView)
        item = lv.highlighted_child
        if isinstance(item, ChainTargetItem):
            self.push_screen(ChainDetailModal(item.chain, self.g))
            return
        if not isinstance(item, ClaimItem):
            return
        claim_text = item.claim_data.get("claim_text", "")
        source_text = item.claim_data.get("source_text", "")
        if not claim_text:
            return
        self.push_screen(ClaimTextModal(claim_text, source_text))

    def action_reset(self) -> None:
        self.current_node = None
        self.state = "search"
        self._nav_stack.clear()
        self._current_steps.clear()
        self._pending_step = None
        self._chain_prefix = None
        self._chain_prefix_subject = None
        self._refresh_history_tree()
        self._list_label("Type a query and press Enter to search.")
        lv = self.query_one("#results-list", ListView)
        lv.clear()
        inp = self.query_one("#search-box", Input)
        inp.value = ""
        inp.focus()


if __name__ == "__main__":
    app = KGBrowser()
    app.run(mouse=False)
