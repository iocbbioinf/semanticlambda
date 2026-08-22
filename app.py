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

from optimal_lambda import LamTerm, LamVar, LamApp, LamAbs
from kg_store import (
    load_graph, load_readings_db, save_readings_db, load_readings_ontologies,
    node_label, node_description, esc, search_nodes,
    get_claims_for_subject, get_claims_for_object, get_mappings_for_subject,
    local_name,
)
from term_utils import (
    collect_edge_claims, append_lambda_term,
    _term_type, _top_claim_name, _render_lam_root,
    title_for_abstraction, inner_abstractions,
)
from widgets import (
    NodeItem, ClaimItem, ReverseClaimItem, SeparatorItem, MappingItem,
    OntologyStepItem,
    ChainTargetItem, ReadingItem, PointerItem,
    ClaimTextModal, ReadingDetailModal, LambdaAbstractionModal,
    OntologyModal, ReadingOntologiesModal, ReflectionModal,
    LambdaBrowserModal, NameReadingModal,
)
from reading_state import (
    PointerSet, LamFan, EntityRegistry, subterm_at, replace_at,
    lam_to_dict_shared, lam_from_dict_shared,
)
import ontology_state as ont_layer


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

    #refl-list-label {
        background: $secondary;
        color: $text;
        padding: 0 1;
        height: 1;
        text-style: italic;
    }

    #refl-list {
        height: auto;
        max-height: 30%;
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
        Binding("p", "focus_reflections", "Positions (Pr)", show=True),
        Binding("s", "save_reading", "Save reading", show=True),
        Binding("a", "lambda_abstraction", "Ask question", show=True),
        Binding("f", "reflection", "Reflection", show=True),
        Binding("o", "ontology_view", "Ontologies", show=True),
        Binding("d", "ontology_demo", "Ontology demo", show=True),
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
        # closed readings keep their ontology sets (§1): name -> [raw dict]
        self.reading_ontologies: dict[str, list] = load_readings_ontologies()
        self._current_term: Optional[LamTerm] = None    # term being built; None = bare current_node
        # Pr — the open reading's pointer set (reading_desc §1). None until a
        # reading starts; opens with exactly one pointer at the root (§3).
        self._pointers: Optional[PointerSet] = None
        # the entity each pointer stands at, by pid: navigation uses the shared
        # subterm's own type, never the reflected cast (§4.2, clarified 08-20)
        self._pointer_nodes: dict[int, rdflib.URIRef] = {}
        # one LamVar per entity, so an entity the user REUSES is one shared node
        # in the reading's graph rather than a fresh variable each time (§1).
        self._entities = EntityRegistry()
        # the reading's ontology set — models in which it is valid (§8).
        self._ontologies: list = []
        self._ont_stats: dict = {}
        self._term_history: list[
            tuple[Optional[LamTerm], rdflib.URIRef,
                  Optional[PointerSet], dict[int, rdflib.URIRef], list]
        ] = []

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
                yield Static("Reflections", id="refl-list-label")
                yield ListView(id="refl-list")
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
        self.query_one("#refl-list-label").display = False
        self.query_one("#refl-list").display = False

    def _hide_search(self) -> None:
        inp = self.query_one("#search-box", Input)
        inp.display = False
        inp.disabled = True
        self.query_one("#history-scroll").display = True
        self.query_one("#to-list-label").display = True
        self.query_one("#to-list").display = True
        self.query_one("#refl-list-label").display = True
        self.query_one("#refl-list").display = True

    # ── Helper: current term ──────────────────────────────────────────────────

    def _current_term_as_lam(self) -> LamTerm:
        """Return _current_term if set, else LamVar for the current node."""
        if self._current_term is not None:
            return self._current_term
        return self._entity_var(self.current_node)

    def _entity_var(self, node, label: str | None = None) -> LamVar:
        """The reading's variable for an entity — the SAME object on reuse.

        An entity used twice in one reading is one entity, so it is one node in
        the sharing graph (§1). Sharing here is by identity, not by a fan:
        reflection's fan carries two casts over one subject, whereas entity reuse
        has nothing to cast — it is simply the same place turning up again.
        """
        iri = str(node)
        return self._entities.get(iri, label or node_label(self.g, node))

    # ── Pointer set  (reading_desc §1, §4) ────────────────────────────────────

    def _start_pointers(self, node: rdflib.URIRef) -> None:
        """Open a reading: Pr = exactly one pointer, at the root (§3)."""
        self._pointers = PointerSet.initial()
        self._pointer_nodes = {}
        self._entities = EntityRegistry()
        act = self._pointers.act()
        if act is not None:
            self._pointer_nodes[act.pid] = node
            # init form (a): ontologies = { ont(G(a), {p}) }, then enrich (§8.3)
            root = self._entity_var(node)
            self._ontologies = ont_layer.init_from_type(root, act.pid)
            self._ontologies, dropped = ont_layer.cap(self._ontologies)
            # The §8.3(a) seed and the reading's own member have the same term at
            # init but different roles, so BOTH stay: the seed is a CANDIDATE, to
            # be enriched and tested by §8.6/§8.7; the reading's member is a
            # MIRROR, never tested (§8.1b). Dropping the seed as a duplicate
            # would leave the set with nothing enrichment could add to, and since
            # enrichment only ever grows what is already there, the set would
            # stay closed for the whole reading.
            # the reading is a member of its own ontology set, from the outset
            self._ontologies = ont_layer.sync_reading_ontology(
                self._ontologies, root, {act.pid: ()})
            self._ont_stats = {"init": len(self._ontologies), "dropped": dropped}

    # ── Ontology set  (reading_desc §8) ───────────────────────────────────────

    def _ont_pointer_paths(self) -> dict:
        """The reading's own pointer paths, keyed by pid — for its mirror in the
        ontology set, whose pointers correspond to the reading's trivially."""
        ps = self._pointers
        if ps is None:
            return {}
        return {p.pid: p.path for p in ps.pointers}

    def _ont_type_at(self, term, path):
        """Type at a position — the typing of §2, used by the ontology rules."""
        return _term_type(subterm_at(term, path))

    def _operand_ontologies(self, name: str) -> list:
        """The stored ontology set of a closed reading used as an operand (§8.6)."""
        return [ont_layer.Ontology.from_dict(d)
                for d in self.reading_ontologies.get(name, [])]

    def _inherit_ontologies(self, name: str) -> None:
        """Init form (b): take the closed reading's STORED ontologies (§8.3).

        Not re-derived — they are survivors of refutation over that reading's
        whole construction (§1). Their pointers collapse to the single root
        pointer, mirroring what closing did to the reading's own set.
        """
        stored = self._operand_ontologies(name)
        if not stored:
            return
        act = self._pointers.act() if self._pointers else None
        if act is None:
            return
        self._ontologies = ont_layer.init_from_closed(stored, act.pid)
        self._ontologies, dropped = ont_layer.cap(self._ontologies)
        self._ont_stats = {"inherited": len(self._ontologies), "dropped": dropped}

    def _ont_after_contraction(self, pid: int, option: int,
                               a_iri: str, b_iri: str,
                               operand_onts=None, operand=None) -> None:
        """UPDATE then ENRICH, in step with the reading's contraction (§8.6)."""
        # the reading's own mirror is not a hypothesis to be tested: keep it out
        # of the update rules and refresh it afterwards.
        candidates = ont_layer.strip_reading_ontology(self._ontologies)
        # A contraction also PROPOSES models: for each saved abstraction of the
        # type at the pointer, app(q, operand) — a rule-1 redex whose firing IS
        # this contraction. Done here rather than at init because the operand is
        # only known once the user has chosen it.
        if operand is not None:
            candidates = candidates + ont_layer.candidates_for_contraction(
                pid, self._ont_pointer_paths().get(pid, ()),
                a_iri, b_iri, operand)
        self._ontologies, stats = ont_layer.after_contraction(
            candidates, pid, option, a_iri, b_iri,
            self._ont_type_at, operand_onts,
        )
        self._ontologies = ont_layer.enrich(self._ontologies)
        self._ontologies, dropped = ont_layer.cap(self._ontologies)
        self._ontologies = ont_layer.sync_reading_ontology(
            self._ontologies, self._current_term_as_lam(), self._ont_pointer_paths())
        stats["dropped"] = dropped
        stats["total"] = len(self._ontologies)
        self._ont_stats = stats
        self._report_ontologies()

    def _ont_after_reflection(self, pid: int, left_pid: int, right_pid: int,
                              a_iri: str, b_iri: str, c_iri: str) -> None:
        """UPDATE then ENRICH, in step with the reading's reflection (§8.7)."""
        candidates = ont_layer.strip_reading_ontology(self._ontologies)
        self._ontologies, stats = ont_layer.after_reflection(
            candidates, pid, left_pid, right_pid,
            a_iri, b_iri, c_iri, self._ont_type_at,
        )
        self._ontologies = ont_layer.enrich(self._ontologies)
        self._ontologies, dropped = ont_layer.cap(self._ontologies)
        self._ontologies = ont_layer.sync_reading_ontology(
            self._ontologies, self._current_term_as_lam(), self._ont_pointer_paths())
        stats["dropped"] = dropped
        stats["total"] = len(self._ontologies)
        self._ont_stats = stats
        self._report_ontologies()

    def _report_ontologies(self) -> None:
        """Surface the state of the set.

        The reading is itself a member (see `ontology_state.reading_ontology`),
        so while a reading exists the set is never empty. What matters now is how
        many CANDIDATE models remain besides it: none means no available explicit
        material accounts for what the user is doing — the §8.8 signal, which the
        reading's own mirror does not mask because it never fires.
        """
        st = self._ont_stats or {}
        candidates = [o for o in self._ontologies if not o.is_reading]
        if not candidates:
            self.notify(
                "Only the reading itself models this reading — no candidate "
                "model accounts for it yet (press o)",
                severity="warning", timeout=8,
            )
            return
        bits = []
        for k in ("fired", "silent", "grafted", "refuted"):
            if st.get(k):
                bits.append(f"{k} {st[k]}")
        if st.get("dropped"):
            bits.append(f"capped −{st['dropped']}")
        detail = f"  ({', '.join(bits)})" if bits else ""
        self.notify(f"Ontologies: {len(self._ontologies)}"
                    f" ({len(candidates)} candidate"
                    f"{'' if len(candidates)==1 else 's'} + the reading)"
                    f"{detail}")

    def _ensure_pointers(self) -> PointerSet:
        """Pr, created on the first step of a reading (§3: one pointer, at root)."""
        if self._pointers is None:
            self._pointers = PointerSet.initial()
            act = self._pointers.act()
            if act is not None and self.current_node is not None:
                self._pointer_nodes[act.pid] = self.current_node
        return self._pointers

    def _act_subterm(self) -> LamTerm:
        """The subterm at actPtr — what a step acts on."""
        ps = self._pointers
        term = self._current_term_as_lam()
        if ps is None:
            return term
        act = ps.act()
        if act is None:
            return term
        sub = subterm_at(term, act.path)
        return sub if sub is not None else term

    def _replace_act_subterm(self, new_sub: LamTerm) -> None:
        """Put `new_sub` where actPtr designates, rebuilding the whole term."""
        ps = self._pointers
        term = self._current_term_as_lam()
        if ps is None or ps.act() is None:
            self._current_term = new_sub
            return
        self._current_term = replace_at(term, ps.act().path, new_sub)

    def _act_node(self) -> Optional[rdflib.URIRef]:
        """The entity actPtr stands at — what the claims panels are built from.

        This is the SHARED SUBTERM'S OWN type, never a reflected cast (§4.2,
        §4.2): after reflecting A -> B at a subterm of type C,
        both branches continue from C.
        """
        ps = self._pointers
        if ps is not None:
            act = ps.act()
            if act is not None and act.pid in self._pointer_nodes:
                return self._pointer_nodes[act.pid]
        return self.current_node

    def _select_pointer(self, pid: int) -> None:
        """Make `pid` the actPtr and rebuild the claims panels around it (§1)."""
        ps = self._pointers
        if ps is None or not ps.select(pid):
            return
        node = self._act_node()
        if node is None:
            self._refresh_reflections_panel()
            return
        # Rebuild both contraction panels for the newly selected position, so the
        # next contraction is bound to THIS pointer.
        self._show_node_and_claims(node, push_stack=False)
        p = ps.get(pid)
        if p is not None and p.cast_type:
            self.notify(
                f"actPtr → {node_label(self.g, node)}  "
                f"(reflected as {local_name(p.cast_type)})"
            )
        else:
            self.notify(f"actPtr → {node_label(self.g, node)}")

    def _refresh_reflections_panel(self) -> None:
        """Render Pr — the reader's live positions — marking actPtr (§1)."""
        lv = self.query_one("#refl-list", ListView)
        label = self.query_one("#refl-list-label", Static)
        lv.clear()
        ps = self._pointers
        if ps is None or not ps.pointers:
            label.update("Reflections [dim]— f: reflect (forks the position)[/dim]")
            return
        term = self._current_term_as_lam()
        n = len(ps.pointers)
        plural = "position" if n == 1 else "positions"
        label.update(
            f"Reflections [b]{n}[/b] {plural} "
            f"[dim]— Enter: make it actPtr  |  f: reflect[/dim]"
        )
        act_index = 0
        for i, p in enumerate(ps.pointers):
            sub = subterm_at(term, p.path)
            node = self._pointer_nodes.get(p.pid)
            stay_lbl = node_label(self.g, node) if node is not None else "?"
            lv.append(PointerItem(
                p, stay_lbl, str(sub) if sub is not None else "?",
                is_act=(p.pid == ps.act_pid),
            ))
            if p.pid == ps.act_pid:
                act_index = i
        lv.call_after_refresh(setattr, lv, "index", act_index)

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

    @staticmethod
    def _abstractions_of(term) -> list:
        """Every abstraction in `term`, the outermost included.

        `inner_abstractions` deliberately skips the top node; here the top one is
        a question too, so it is added back.
        """
        out = list(inner_abstractions(term))
        if isinstance(term, LamAbs):
            out.insert(0, term)
        return out

    def _ont_proposed_steps(self, reverse: bool) -> list[tuple]:
        """Steps the ONTOLOGY SET offers at actPtr, as (node, label, n, titles).

        These are contractions for which a MODEL already exists even though the
        knowledge graph asserts no claim: some ontology has app(ta, tb) with
        actPtr at one side and the offered entity's type at the other (§8.1).
        Entities the claim panels already list are dropped — the point is to add
        what the graph does not say, not to duplicate what it does.
        """
        ps = self._pointers
        if ps is None or not self._ontologies:
            return []
        act = ps.act()
        if act is None:
            return []
        cands = ont_layer.strip_reading_ontology(self._ontologies)
        found = ont_layer.steps_proposed(cands, act.pid, self._ont_type_at,
                                        reverse=reverse) if cands else {}
        # Sources of a title, per offered entity.
        sources: dict[str, list] = {}
        for iri, onts in found.items():
            qs = []
            for o in onts:
                qs.extend(self._abstractions_of(o.origin_term))
            sources[iri] = qs

        # AT THE START OF A READING no ontology holds an application yet (§8.3
        # seeds a bare variable), so `steps_proposed` finds nothing. The offers
        # are then read off the saved QUESTIONS of the pointer's own type — the
        # same offer one step earlier (§8.7b).
        if not found:
            here = self._act_subterm()
            t_iri = _term_type(here) if here is not None else None
            if t_iri:
                by_q = ont_layer.steps_from_questions(
                    t_iri, self._ont_type_at, reverse=reverse)
                for iri, qs in by_q.items():
                    found[iri] = qs
                    sources[iri] = list(qs)

        out = []
        for iri, backers in sorted(found.items()):
            node = rdflib.URIRef(iri)
            titles = []
            for q in sources.get(iri, []):
                t = title_for_abstraction(q)
                if t and t not in titles:
                    titles.append(t)
            out.append((node, node_label(self.g, node), len(backers), titles))
        return out

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
            proposed = self._ont_proposed_steps(reverse=False)
            if proposed:
                self._list_label(
                    f"No outgoing claims for [b]{esc(lbl)}[/b] — but the "
                    f"ontologies propose steps.  Enter: follow:"
                )
                lv.append(SeparatorItem("Proposed by the ontologies (no claim)"))
                for nd, nlbl, n, titles in proposed:
                    lv.append(OntologyStepItem(nd, nlbl, n, titles))
                lv.focus()
                lv.call_after_refresh(setattr, lv, "index", 0)
            else:
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
            seen_out = set()
            for cd in claims:
                lv.append(ClaimItem(cd))
                if isinstance(cd["object"], rdflib.URIRef):
                    seen_out.add(cd["object"])
            if mappings:
                lv.append(SeparatorItem("External Mappings"))
                for md in mappings:
                    lv.append(MappingItem(md))
            if matching:
                lv.append(SeparatorItem("Compose with saved reading"))
                for idx, name, term, pred, ciri in matching:
                    lv.append(ChainTargetItem(name, term, idx, pred, ciri))
            # Steps some ONTOLOGY already contains at actPtr, which no claim
            # asserts: a model exists for them, so the reading may take them.
            proposed = [p for p in self._ont_proposed_steps(reverse=False)
                        if p[0] not in seen_out]
            if proposed:
                lv.append(SeparatorItem("Proposed by the ontologies (no claim)"))
                for nd, nlbl, n, titles in proposed:
                    lv.append(OntologyStepItem(nd, nlbl, n, titles))
            lv.focus()
            lv.call_after_refresh(setattr, lv, "index", 0)

        to_lv = self.query_one("#to-list", ListView)
        to_lv.clear()
        to_label = self.query_one("#to-list-label", Static)
        matching_rev = self._readings_matching_reverse(node)
        seen_in = {rc["subject"] for rc in reverse_claims
                   if isinstance(rc["subject"], rdflib.URIRef)}
        proposed_rev = [p for p in self._ont_proposed_steps(reverse=True)
                        if p[0] not in seen_in]
        if reverse_claims or matching_rev or proposed_rev:
            hint = "s: save reading"
            if reverse_claims or proposed_rev:
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
            if proposed_rev:
                # actPtr at tb, [ta] == the offered entity: option 2, the reader
                # STAYS and the proposal goes into function position behind it.
                to_lv.append(SeparatorItem("Proposed by the ontologies (no claim)"))
                for nd, nlbl, n, titles in proposed_rev:
                    to_lv.append(OntologyStepItem(nd, nlbl, n, titles, reverse=True))
            to_lv.call_after_refresh(setattr, to_lv, "index", 0)
        else:
            to_label.update(f"Claims to [b]{esc(lbl)}[/b]:")

        self._refresh_reflections_panel()
        self.state = "claims_list"

    # ── Term snapshot ─────────────────────────────────────────────────────────

    def _push_term_snapshot(self) -> None:
        import copy
        self._term_history.append((
            self._current_term, self.current_node,
            copy.deepcopy(self._pointers), dict(self._pointer_nodes),
            list(self._ontologies),
        ))

    # ── Contraction  (reading_desc §4.1) ──────────────────────────────────────

    def _contract(self, operand: LamTerm, option: int,
                  land_node: rdflib.URIRef, operand_onts=None) -> None:
        """Apply a contraction at actPtr.

        BOTH OPTIONS BUILD app(t1, t2) with [t1] == A and [t2] == B (§4.1). They
        differ only in which of the two actPtr designates:

          option 1 — actPtr is at t1 [A]; the operand is t2 [B].
                     app(t1, operand) — the reader MOVES to B.
          option 2 — actPtr is at t2 [B]; the operand is t1 [A].
                     app(operand, t2) — the reader STAYS at B, type unchanged.

        So A and B name positions in the built term, not the option — which is
        what makes §8.6's type test uniform across both.

        Both act ONLY at actPtr and leave every other pointer designating the
        same material (§4.1, I2). `land_node` is the entity the pointer stands at
        afterwards, and is what the claims panels are rebuilt from.
        """
        ps = self._ensure_pointers()
        act = ps.act()
        stayed = self._act_subterm()
        if option == 1:
            # actPtr is t1 [A]; the operand is t2 [B]
            t1, t2 = stayed, operand
        else:
            # actPtr is t2 [B]; the operand is t1 [A]
            t1, t2 = operand, stayed
        new_sub = LamApp(t1, t2)
        a_iri = _term_type(t1)
        b_iri = _term_type(t2)
        self._replace_act_subterm(new_sub)
        if act is not None:
            ps.after_contraction(act, option)
            self._pointer_nodes[act.pid] = land_node
            # the ontology layer moves in step: this contraction IS the user's
            # approval of a rule-1 firing (§8.5(3), §8.6).
            self._ont_after_contraction(act.pid, option, a_iri, b_iri,
                                        operand_onts=operand_onts,
                                        operand=operand)

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
            # init form (b): re-open a closed reading with ONE pointer at the
            # root (§3(b)); the equivalence of §1 makes this not a special rule.
            type_iri = _term_type(item.reading_term)
            if not type_iri:
                return
            self._current_term = item.reading_term
            self._start_pointers(rdflib.URIRef(type_iri))
            self._inherit_ontologies(item.reading_name)
            self._hide_search()
            self._show_node_and_claims(rdflib.URIRef(type_iri))
            return

        if self.state == "node_list" and isinstance(item, NodeItem):
            # init form (a): the user selects a type; the reading opens with one
            # pointer at that entity (§3(a)).
            self._start_pointers(item.node)
            self._hide_search()
            self._show_node_and_claims(item.node)

        # ── Reflections panel: choose actPtr (§1) ──────────────────────────
        if lv_id == "refl-list" and isinstance(item, PointerItem):
            self._select_pointer(item.pid)
            return

        if self.state == "claims_list" and isinstance(item, ClaimItem):
            cd = item.claim_data
            next_node = cd["object"]
            if not isinstance(next_node, rdflib.URIRef):
                self._list_label("Claim object is a literal — cannot navigate. Ctrl+R to search again.")
                return
            self._push_term_snapshot()
            # option 1: A asks, B answers — the reader moves to B
            self._contract(
                self._entity_var(next_node, cd["object_label"]),
                option=1, land_node=next_node,
            )
            self._show_node_and_claims(next_node)

        elif self.state == "claims_list" and isinstance(item, MappingItem):
            md = item.mapping_data
            next_node = md["object"]
            if not isinstance(next_node, rdflib.URIRef):
                self._list_label("Mapping object is a literal — cannot navigate. Ctrl+R to search again.")
                return
            self._push_term_snapshot()
            self._contract(
                self._entity_var(next_node, md["object_label"]),
                option=1, land_node=next_node,
            )
            self._show_node_and_claims(next_node)

        elif isinstance(item, OntologyStepItem):
            # A step the ontologies already contain. Following it is an ORDINARY
            # contraction — the option is fixed by which side actPtr is on:
            #   forward  actPtr at ta, [tb] == the entity  -> option 1, reader MOVES
            #   reverse  actPtr at tb, [ta] == the entity  -> option 2, reader STAYS
            self._push_term_snapshot()
            if item.reverse:
                stay_node = self._act_node()
                self._contract(self._entity_var(item.node, item.label_text),
                               option=2, land_node=stay_node)
                self._show_node_and_claims(stay_node, push_stack=False)
            else:
                self._contract(self._entity_var(item.node, item.label_text),
                               option=1, land_node=item.node)
                self._show_node_and_claims(item.node)

        elif lv_id == "to-list" and isinstance(item, ReverseClaimItem):
            cd = item.claim_data
            a_node = cd["subject"]
            if not isinstance(a_node, rdflib.URIRef):
                self._list_label("Claim subject is a literal — cannot navigate. Ctrl+R to search again.")
                return
            self._push_term_snapshot()
            stay_node = self._act_node()
            # option 2: A asks, B answers — the reader was already at B and
            # stays; the selected A goes into function position behind it (§4.1)
            self._contract(
                self._entity_var(a_node, cd["subject_label"]),
                option=2, land_node=stay_node,
            )
            self._show_node_and_claims(stay_node, push_stack=False)

        elif lv_id == "to-list" and isinstance(item, ChainTargetItem):
            # A closed reading in FUNCTION position would be option 2, which is
            # ENTITY-ONLY by intent (§4.1). Refuse rather than build it.
            self.notify(
                "Option 2 takes an entity only — a saved reading may be an "
                "answer, never a question (§4.1)",
                severity="warning", timeout=6,
            )
            return

        elif self.state == "claims_list" and isinstance(item, ChainTargetItem):
            self._push_term_snapshot()
            tail_iri = _term_type(item.reading_term)
            tail_node = rdflib.URIRef(tail_iri) if tail_iri else self._act_node()
            # option 1 with a CLOSED READING operand — permitted (§4.1). Its
            # own ontology set is paired with the current one (§8.6).
            self._contract(item.reading_term, option=1, land_node=tail_node,
                           operand_onts=self._operand_ontologies(item.reading_name))
            self._nav_stack.clear()
            type_lbl = local_name(tail_iri) if tail_iri else "?"
            self.notify(f"Composed — continuing from: {type_lbl}")
            self._show_node_and_claims(tail_node)

    # ── Key handlers ─────────────────────────────────────────────────────────

    def action_save_reading(self) -> None:
        self._handle_save_reading()

    def action_lambda_abstraction(self) -> None:
        self._handle_lambda_abstraction()

    def action_reflection(self) -> None:
        self._handle_reflection()

    def action_ontology_view(self) -> None:
        """The ontologies of the ACTUAL reading (§8)."""
        has_reading = self._current_term is not None or self.current_node is not None
        self.push_screen(ReadingOntologiesModal(
            self.g, self._ontologies, has_reading, self._ont_stats))

    def action_ontology_demo(self) -> None:
        """The standalone demo ontology term — not tied to any reading."""
        self.push_screen(OntologyModal(self.g))

    def action_lambda_browser(self) -> None:
        self.push_screen(LambdaBrowserModal(self.g))

    def _handle_reflection(self) -> None:
        """Reflection step: fork the position over a shared subject (§4.2)."""
        if self.state != "claims_list" or self.current_node is None:
            self.notify("Start a reading first", severity="warning")
            return
        stay_node = self._act_node()
        stay_label = node_label(self.g, stay_node) if stay_node else "?"

        def on_reflection(result) -> None:
            if result is None:
                return
            a_iri, a_lbl, b_iri, b_lbl = result
            self._push_term_snapshot()

            ps = self._ensure_pointers()
            act = ps.act()
            # G(t) |-> a SHARING FAN-IN over t (§4.2). No fresh variables: both
            # aux ports ARE the shared subject, held once in the graph.
            #
            # The fan is an explicit node, not LamApp(t, t): object identity
            # alone does not survive saving or copying, so sharing has to be
            # structural for the reading to really be a sharing graph (I5).
            t = self._act_subterm()
            self._replace_act_subterm(
                LamFan(principal=t, grey_cast=a_iri, black_cast=b_iri)
            )
            if act is not None:
                c_iri = _term_type(t)
                left, right = ps.after_reflection(act, a_iri, b_iri)
                # Navigation continues from C for BOTH branches, so both
                # pointers stand at the same node the user was already at.
                node = self._pointer_nodes.get(act.pid, stay_node)
                self._pointer_nodes.pop(act.pid, None)
                self._pointer_nodes[left.pid] = node
                self._pointer_nodes[right.pid] = node
                # this reflection IS the user's approval of a rule-4 firing
                self._ont_after_reflection(act.pid, left.pid, right.pid,
                                           a_iri, b_iri, c_iri)
            self.notify(
                f"Reflected: {a_lbl} (question) → {b_lbl} (answer) "
                f"at {stay_label}", timeout=6,
            )
            self._show_node_and_claims(stay_node, push_stack=False)

        self.push_screen(ReflectionModal(self.g, stay_label), on_reflection)

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
            # closing drops the POINTER SET only — the ontologies are kept (§1)
            self.reading_ontologies[name] = [
                o.to_dict() for o in self._ontologies
            ]
            save_readings_db(self.readings, self.reading_ontologies)
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

    def action_focus_reflections(self) -> None:
        """Focus Pr so a position can be chosen as actPtr (§1)."""
        if self.state != "claims_list":
            return
        lv = self.query_one("#refl-list", ListView)
        if not lv.children:
            self.notify("No positions yet — press f to reflect", severity="warning")
            return
        lv.focus()

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
        term, node, pointers, pointer_nodes, ontologies = self._term_history.pop()
        self._current_term = term
        self._pointers = pointers
        self._pointer_nodes = pointer_nodes
        self._ontologies = ontologies
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
        self._pointers = None
        self._pointer_nodes.clear()
        self._entities = EntityRegistry()
        self._ontologies = []
        self._ont_stats = {}
        self._term_history.clear()
        self._refresh_history_tree()
        self._list_label("Type a query and press Enter to search.")
        self.query_one("#results-list", ListView).clear()
        self.query_one("#to-list", ListView).clear()
        self.query_one("#to-list-label", Static).update("Claims to…")
        self.query_one("#refl-list", ListView).clear()
        self.query_one("#refl-list-label", Static).update("Reflections")
        self.query_one("#search-box", Input).value = ""
        self._show_search()
