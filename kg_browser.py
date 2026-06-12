#!/usr/bin/env python3
"""Knowledge Graph Browser — terminal app for exploring the AHoJ RDF knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pyperclip
import rdflib
from rdflib.namespace import RDFS
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static
from textual.containers import VerticalScroll, ScrollableContainer, Horizontal, Vertical

RDF_FILE = Path(__file__).parent / "data" / "ahoj_reduced_external_kb_fixed_with_glycogen_phosphorylase_enriched_claims.rdf"
TTL_FILE = Path(__file__).parent / "data" / "ahoj_reduced_external_kb_fixed_with_glycogen_phosphorylase_enriched_claims.ttl"

AHOJ = rdflib.Namespace("https://w3id.org/ahoj/ontology/")


def load_graph() -> rdflib.Graph:
    g = rdflib.Graph()
    if RDF_FILE.exists():
        g.parse(str(RDF_FILE), format="xml")
    else:
        g.parse(str(TTL_FILE), format="turtle")
    return g


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

    ENT_NS = "https://w3id.org/ahoj/entity/"
    CLAIM_NS = "https://w3id.org/ahoj/claim/"

    mapping_nodes: set[rdflib.URIRef] = set(g.subjects(rdflib.RDF.type, AHOJ.ExternalMapping))

    candidates: set[rdflib.URIRef] = set()
    for s, _p, o in g:
        if isinstance(s, rdflib.URIRef):
            candidates.add(s)
        if isinstance(o, rdflib.URIRef):
            candidates.add(o)

    scored: list[tuple[float, rdflib.URIRef]] = []
    for node in candidates:
        if node in mapping_nodes:
            continue
        iri = str(node)
        if iri.startswith(CLAIM_NS):
            continue
        lbl = node_label(g, node).lower()
        local = iri.rsplit("/", 1)[-1].replace("_", " ").lower()
        text = lbl + " " + local
        hits = sum(1 for w in query_words if w in text)
        if hits == 0:
            continue
        boost = 2.0 if iri.startswith(ENT_NS) else 0.0
        scored.append((hits + boost, node))

    scored.sort(key=lambda x: -x[0])
    return [n for _score, n in scored[:limit]]


def get_claims_for_subject(g: rdflib.Graph, subject: rdflib.URIRef) -> list[dict]:
    claims = []
    for claim in g.subjects(AHOJ.claimSubject, subject):
        claim_text = str(g.value(claim, AHOJ.claimText) or "")
        source_text = str(g.value(claim, AHOJ.sourceText) or "")
        obj = g.value(claim, AHOJ.claimObject)
        pred = g.value(claim, AHOJ.claimPredicate)
        pred_label = str(pred).rsplit("/", 1)[-1].replace("_", " ") if pred else "?"
        obj_lbl = node_label(g, obj) if isinstance(obj, rdflib.URIRef) else str(obj or "?")
        obj_types = node_types(g, obj) if isinstance(obj, rdflib.URIRef) else frozenset()
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


def get_mappings_for_subject(g: rdflib.Graph, subject: rdflib.URIRef) -> list[dict]:
    mappings = []
    for mapping in g.subjects(AHOJ.mappingSubject, subject):
        obj = g.value(mapping, AHOJ.mappingObject)
        pred = g.value(mapping, AHOJ.mappingPredicate)
        pred_label = str(pred).rsplit("#", 1)[-1].rsplit("/", 1)[-1].replace("_", " ") if pred else "?"
        obj_lbl = node_label(g, obj) if isinstance(obj, rdflib.URIRef) else str(obj or "?")
        obj_types = node_types(g, obj) if isinstance(obj, rdflib.URIRef) else frozenset()
        if pred is None or "exactMatch" not in str(pred):
            continue
        mappings.append({
            "mapping": mapping,
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
        Binding("right", "to_source", "Source text"),
        Binding("left", "dismiss", "Back to claims"),
        Binding("c", "copy", "Copy"),
    ]

    def __init__(self, claim_text: str, source_text: str) -> None:
        super().__init__()
        self._claim_text = claim_text
        self._source_text = source_text

    def compose(self) -> ComposeResult:
        hint = "→ source  |  ← back  |  c copy  |  Esc close"
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
            yield Static(f"Source Text  [dim]({hint})[/dim]", id="modal-title", markup=True)
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

    def __init__(self, chain: Chain) -> None:
        super().__init__()
        self._chain = chain

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="modal-container"):
            yield Static(
                f"Chain: [b]{esc(self._chain.label)}[/b]  [dim](type: {esc(self._chain.type_label)}  |  Esc close)[/dim]",
                id="modal-title", markup=True,
            )
            lines: list[str] = []
            _render_chain(self._chain, lines, indent=0)
            yield Static("\n".join(lines), id="modal-body", markup=True)


class ClaimItem(ListItem):
    def __init__(self, claim_data: dict) -> None:
        iri = str(claim_data['claim']).rsplit("/", 1)[-1]
        display = f"[b]{esc(claim_data['predicate_label'])}[/b]  →  {esc(claim_data['object_label'])}  [dim]{esc(iri)}[/dim]"
        super().__init__(Label(display, markup=True))
        self.claim_data = claim_data


class SeparatorItem(ListItem):
    def __init__(self, label: str) -> None:
        super().__init__(Label(f"[dim]─── {esc(label)} ───[/dim]", markup=True))
        self.disabled = True


class MappingItem(ListItem):
    def __init__(self, mapping_data: dict) -> None:
        iri = str(mapping_data['mapping']).rsplit("/", 1)[-1]
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

    .history-node {
        padding: 0 1;
        background: $surface;
        color: $text;
    }

    .history-claim {
        padding: 0 1 0 3;
        background: $surface-darken-1;
        color: $text-muted;
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

    ListItem { padding: 0 1; }
    ListItem.--highlight { background: $accent 30%; }
    """

    BINDINGS = [
        Binding("ctrl+r", "reset", "New search"),
        Binding("right", "show_source", "Claim text / Chain detail", show=True),
        Binding("left", "go_back", "Back", show=True),
        Binding("c", "copy_item", "Copy IRI", show=True),
        Binding("tab", "focus_chains", "Chains panel", show=True),
        Binding("q", "app.quit", "Quit"),
    ]

    state: reactive[str] = reactive("search")

    def __init__(self) -> None:
        super().__init__()
        self.g = load_graph()
        self.current_node: Optional[rdflib.URIRef] = None
        self._nav_stack: list[tuple[rdflib.URIRef, int]] = []
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
        yield VerticalScroll(id="history-scroll")
        with Horizontal(id="main-area"):
            with Vertical(id="left-pane"):
                yield Static("Type a query and press Enter to search.", id="list-label")
                yield ListView(id="results-list")
            with Vertical(id="right-pane"):
                yield Static("Chains", id="chains-label")
                yield ListView(id="chains-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#search-box", Input).focus()

    # ── History helpers ───────────────────────────────────────────────────────

    def _append_history(self, markup: str, css_class: str) -> None:
        scroll = self.query_one("#history-scroll", VerticalScroll)
        scroll.mount(Static(markup, markup=True, classes=css_class))
        scroll.scroll_end(animate=False)

    def _clear_history(self) -> None:
        scroll = self.query_one("#history-scroll", VerticalScroll)
        for child in list(scroll.children):
            child.remove()

    def _push_node(self, node: rdflib.URIRef) -> None:
        lbl = node_label(self.g, node)
        iri = str(node)
        self._append_history(
            f"[b]▶ {esc(lbl)}[/b]  [dim]{esc(iri)}[/dim]",
            "history-node",
        )

    def _push_claim(self, claim_data: dict) -> None:
        obj_lbl = claim_data["object_label"]
        claim_text = claim_data["claim_text"]
        self._append_history(
            f"  [dim]{esc(claim_text)}[/dim]\n"
            f"  → [b]{esc(obj_lbl)}[/b]",
            "history-claim",
        )

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
            lv.index = min(prev_index or 0, len(self.chains) - 1)

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
            scroll = self.query_one("#history-scroll", VerticalScroll)
            self._nav_stack.append((node, len(scroll.children)))
        self.current_node = node
        lbl = node_label(self.g, node)
        self._push_node(node)

        claims = get_claims_for_subject(self.g, node)
        mappings = get_mappings_for_subject(self.g, node)
        lv = self.query_one("#results-list", ListView)
        lv.clear()
        if not claims and not mappings:
            self._list_label(
                f"No outgoing claims for [b]{esc(lbl)}[/b].  Ctrl+R to search again."
            )
            self.state = "claims_list"
            return
        matching = self._chains_matching_any(claims, mappings)
        if matching:
            self._list_label(
                f"Claims from [b]{esc(lbl)}[/b] — Enter: follow  |  s: save chain  |  select ⛓ to compose:"
            )
        else:
            self._list_label(
                f"Claims from [b]{esc(lbl)}[/b] — Enter: follow  |  s: save chain:"
            )
        for cd in claims:
            lv.append(ClaimItem(cd))
        if mappings:
            lv.append(SeparatorItem("External Mappings"))
            for md in mappings:
                lv.append(MappingItem(md))
        if matching:
            lv.append(SeparatorItem("Compose with saved chain"))
            for idx, chain in matching:
                lv.append(ChainTargetItem(chain, idx))
        self.state = "claims_list"
        lv.focus()
        lv.index = 0

    # ── Step helpers ──────────────────────────────────────────────────────────

    def _make_claim_step(self, cd: dict) -> ChainStep:
        return ChainStep(
            subject=self.current_node,
            subject_label=node_label(self.g, self.current_node),
            predicate_label=cd["predicate_label"],
            object_node=cd["object"],
            object_label=cd["object_label"],
            kind="claim",
            claim_iri=str(cd["claim"]).rsplit("/", 1)[-1],
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
            claim_iri=str(md["mapping"]).rsplit("/", 1)[-1],
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
        lv.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        lv_id = event.list_view.id

        # ── Chains panel selection ─────────────────────────────────────────
        if lv_id == "chains-list" and isinstance(item, ChainListItem):
            self.push_screen(ChainDetailModal(item.chain))
            return

        # ── Main list selections ───────────────────────────────────────────
        if self.state == "node_list" and isinstance(item, NodeItem):
            self._show_node_and_claims(item.node)

        elif self.state == "claims_list" and isinstance(item, ClaimItem):
            cd = item.claim_data
            self._push_claim(cd)
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

        elif self.state == "claims_list" and isinstance(item, ChainTargetItem):
            # Attach selected chain as sub_chain of the last step (or store as prefix
            # if no steps taken yet), then continue navigation from the chain's tail.
            if self._current_steps:
                self._current_steps[-1].sub_chain = item.chain
            else:
                self._chain_prefix = item.chain
                self._chain_prefix_subject = self.current_node
            self._nav_stack.clear()
            self._clear_history()
            tail_node = item.chain.steps[-1].object_node
            self.notify(f"Composed — continuing from: {item.chain.steps[-1].object_label}")
            self._show_node_and_claims(tail_node)

    # ── Key: s = save current navigation path as a new chain ─────────────────

    def on_key(self, event) -> None:
        if event.key != "s":
            return
        if self.state != "claims_list" or not self._current_steps:
            return
        steps = list(self._current_steps)
        if self._chain_prefix is not None:
            steps[0].sub_chain = self._chain_prefix
            if self._chain_prefix_subject is not None:
                steps[0].subject = self._chain_prefix_subject
                steps[0].subject_label = node_label(self.g, self._chain_prefix_subject)
        chain = Chain(steps=steps)
        self.chains.append(chain)
        self._refresh_chains_panel()
        self.notify(f"Chain saved: {chain.label}")
        # reset navigation to start a new path
        self._current_steps.clear()
        self._nav_stack.clear()
        self._pending_step = None
        self._chain_prefix = None
        self._chain_prefix_subject = None
        self._clear_history()
        self._list_label("Chain saved — start a new search:")
        lv = self.query_one("#results-list", ListView)
        lv.clear()
        self.state = "search"
        inp = self.query_one("#search-box", Input)
        inp.value = ""
        inp.focus()

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_focus_chains(self) -> None:
        lv = self.query_one("#chains-list", ListView)
        lv.focus()
        if self.chains and lv.index is None:
            lv.index = 0

    def action_go_back(self) -> None:
        if self.state != "claims_list" or len(self._nav_stack) < 2:
            return
        self._pending_step = None
        self._chain_prefix = None
        self._chain_prefix_subject = None
        if self._current_steps:
            self._current_steps.pop()
        self._nav_stack.pop()
        prev_node, history_count = self._nav_stack[-1]
        scroll = self.query_one("#history-scroll", VerticalScroll)
        for child in list(scroll.children)[history_count:]:
            child.remove()
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
                self.push_screen(ChainDetailModal(item.chain))
            return

        if self.state != "claims_list":
            return
        lv = self.query_one("#results-list", ListView)
        item = lv.highlighted_child
        if isinstance(item, ChainTargetItem):
            self.push_screen(ChainDetailModal(item.chain))
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
        self._clear_history()
        self._list_label("Type a query and press Enter to search.")
        lv = self.query_one("#results-list", ListView)
        lv.clear()
        inp = self.query_one("#search-box", Input)
        inp.value = ""
        inp.focus()


if __name__ == "__main__":
    app = KGBrowser()
    app.run(mouse=False)
