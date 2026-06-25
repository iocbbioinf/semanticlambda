from __future__ import annotations

from typing import Optional

import pyperclip
import rdflib
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Label, ListView, Static
from textual.containers import VerticalScroll, Horizontal, Vertical

from lambda_term import LamTerm, LamVar, LamApp, LamAbs
from kg_store import (
    load_graph, load_readings_db, save_readings_db,
    node_label, node_description, esc, search_nodes,
    get_claims_for_subject, get_claims_for_object, get_mappings_for_subject,
    local_name,
)
from term_utils import (
    collect_edge_claims, append_lambda_term,
    _term_type, _top_claim_name, _render_lam_root,
)
from widgets import (
    NodeItem, ClaimItem, ReverseClaimItem, SeparatorItem, MappingItem,
    ChainTargetItem, ReadingItem,
    ClaimTextModal, ReadingDetailModal, LambdaAbstractionModal,
    OntologyModal, LambdaBrowserModal, NameReadingModal,
)


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
