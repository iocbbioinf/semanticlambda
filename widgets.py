from __future__ import annotations

import pyperclip
import rdflib
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Static
from textual.containers import ScrollableContainer, Horizontal, Vertical

from optimal_lambda import LamTerm, LamVar, LamApp, LamAbs, lam_from_dict
from kg_store import (
    local_name, node_label, esc, search_nodes, load_lambda_db,
)
from optimal_lambda import beta_reduce_sequence
from term_utils import (
    _term_type, _render_lam_root, collect_edge_claims,
    _make_ontology_term, _make_beta_term,
)


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


class NodeItem(ListItem):
    def __init__(self, node: rdflib.URIRef, label: str, description: str = "") -> None:
        if description:
            content = f"{esc(label)}\n[dim]{esc(description)}[/dim]"
        else:
            content = esc(label)
        super().__init__(Label(content, markup=True))
        self.node = node


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
