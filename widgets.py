from __future__ import annotations

from typing import Optional

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
    _make_ontology_term, _make_beta_term, has_non_ai_question, repl_source,
    repl_legend,
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
/* `height: auto`, not `1fr` — see the note on #ont-body. A reading long enough
   to overflow was clipped instead of scrolling (38 lines in a 14-high
   container, max_scroll_y 0). */
#modal-body {
    height: auto;
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
            # no citation to show: → goes straight on to the type tree, so the
            # chain does not dead-end on a claim that has no source text.
            self.dismiss("types")
            return

        def on_source_dismissed(res) -> None:
            if res == "types":
                self.dismiss("types")       # pass the request to the caller
            elif res:
                self.dismiss()
        self.app.push_screen(
            SourceTextModal(self._claim_text, self._source_text),
            on_source_dismissed)

    def action_copy(self) -> None:
        pyperclip.copy(self._claim_text)
        self.notify("Claim text copied")


class SourceTextModal(ModalScreen):
    """The citation. → continues to the TYPE the claim reaches (§2).

    The navigation is one chain: claim -> claim text -> citation -> the entity
    and its question tree. So a contraction operand can be chosen at any depth,
    from the bare entity to a question naming a subtype of it.
    """

    CSS = MODAL_CSS

    BINDINGS = [
        Binding("escape", "close_all", "Close"),
        Binding("q", "close_all", "Close"),
        Binding("left", "dismiss", "Back to claim text"),
        Binding("right", "to_types", "Type / questions"),
        Binding("c", "copy", "Copy"),
    ]

    def __init__(self, claim_text: str, source_text: str) -> None:
        super().__init__()
        self._source_text = source_text

    def compose(self) -> ComposeResult:
        hint = "→ type & questions  |  ← back  |  c copy  |  Esc close"
        with ScrollableContainer(id="modal-container"):
            yield Static(f"Citation  [dim]({hint})[/dim]", id="modal-title", markup=True)
            yield Static(esc(self._source_text), id="modal-body")

    def action_to_types(self) -> None:
        """Ask the caller to open the question tree — it knows the entity."""
        self.dismiss("types")

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
        Binding("o", "ontologies", "Ontologies"),
    ]

    def __init__(self, name: str, term: LamTerm, g: rdflib.Graph,
                 ontologies: list | None = None) -> None:
        super().__init__()
        self._name = name
        self._term = term
        self._g = g
        # The SAVED ontology set of this reading (§1: closing drops the pointer
        # set, not the ontologies). Passed in already decoded, since the caller
        # owns the store; None means the caller had none to give.
        self._ontologies = list(ontologies or [])

    def action_ontologies(self) -> None:
        """Show this reading's stored ontologies — the models it is valid in (§8).

        The same list the live reading's panel shows, over the SAVED set rather
        than the one being built, so a closed reading can still be read as the
        models that account for it.
        """
        if not self._ontologies:
            self.app.notify(f"'{self._name}' has no stored ontologies")
            return
        self.app.push_screen(
            ReadingOntologiesModal(self._g, self._ontologies, True))

    def compose(self) -> ComposeResult:
        type_iri = _term_type(self._term)
        type_lbl = local_name(type_iri) if type_iri else "?"
        claims = collect_edge_claims(self._g, self._term)
        claims_by_edge: dict[tuple[str, str], list[str]] = {}
        for c in claims:
            key = (c["subj_iri"], c["obj_iri"])
            claims_by_edge.setdefault(key, []).append(c["claim_text"])
        with ScrollableContainer(id="modal-container"):
            hint = (f"  |  o: {len(self._ontologies)} ontologies"
                    if self._ontologies else "")
            yield Static(
                f"Reading: [b]{esc(self._name)}[/b]  [dim](type: "
                f"{esc(type_lbl)}{hint}  |  Esc close)[/dim]",
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


def _free_type_iris(term) -> set:
    """The type iris a term actually mentions — what it can legally bind.

    §8.2's Q1: a question's bound variable must occur free in its body. So only
    these are bindable; anything else would be "a question about nothing".
    """
    out = set()

    def walk(x, bound=frozenset()):
        if isinstance(x, LamVar):
            if x.iri not in bound:
                out.add(x.iri)
        elif isinstance(x, LamApp):
            walk(x.func, bound); walk(x.arg, bound)
        elif isinstance(x, LamAbs):
            walk(x.body, bound | {x.var.iri})
        elif hasattr(x, "principal"):
            walk(x.principal, bound)
    walk(term)
    return out


class LambdaAbstractionModal(ModalScreen):
    """Create a question: choose the TYPE its variable will be bound at (§8.2).

    The type may be an ENTITY or a question naming a subtype of one (§2), and
    which it is decides the new question's place in the tree: binding a variable
    of type A1 makes the new question a child of the question naming A1. So this
    is where a SECOND level of the hierarchy gets built, and nothing else can
    build one.

    Search finds the entity; → opens its question tree so a question below it can
    be taken instead. Enter on a search hit binds the entity itself, which is the
    root of that tree and the general case.
    """

    CSS = LAMBDA_MODAL_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
        Binding("right", "to_tree", "Questions of it"),
    ]

    def __init__(self, g: rdflib.Graph, term: LamTerm, tree=None) -> None:
        super().__init__()
        self._g = g
        self._term = term
        self._tree = tree
        self._candidates: list[rdflib.URIRef] = []

    def compose(self) -> ComposeResult:
        hint = ("type to search  |  ↑↓ navigate  |  → its questions  |  "
                "Enter select  |  Esc cancel")
        with Vertical(id="lambda-container"):
            yield Static(
                f"Question  [dim]({hint})[/dim]\n"
                f"Bind a variable at which type?",
                id="lambda-title", markup=True,
            )
            yield Input(placeholder="Search for a node…", id="lambda-search")
            yield ListView(id="lambda-results")

    def action_to_tree(self) -> None:
        """→ on a search hit: choose from its question tree instead.

        The binder may be a QUESTION, which is what makes the hierarchy deeper
        than one level. Without this the tree could only ever be entity-rooted
        questions, and the chains that fire repeatedly could not be built.

        BUT ONLY A TYPE THE READING ALREADY STANDS IN CAN BE BOUND. §8.2's Q1
        demands the bound variable occur free in the body, and the body is the
        reading: so binding at a question requires the reading to have BEEN in
        that question — begun there (§3), or contracted into it by its title
        (§4.1). A type the body never mentions is refused, so it is not offered:
        the tree is filtered to what this reading can actually bind.
        """
        if self._tree is None:
            return
        lv = self.query_one("#lambda-results", ListView)
        item = lv.highlighted_child
        if not isinstance(item, NodeItem):
            return
        iri = str(item.node)
        present = _free_type_iris(self._term)

        def picked(res) -> None:
            if res is None:
                return
            type_iri, label = res
            if type_iri not in present:
                self.app.notify(
                    "The reading does not stand in that question — contract it "
                    "in first, then bind (§8.2 Q1).", severity="warning",
                    timeout=8)
                return
            var = LamVar(iri=type_iri, label=label)
            self.dismiss(LamAbs(var=var, body=self._term))

        self.app.push_screen(
            QuestionTreeModal(self._tree, iri,
                              node_label(self._g, item.node),
                              "Bind a variable at", bindable=present),
            picked)

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


# How many ontologies the list RENDERS. A display bound only: the set itself is
# never trimmed (O7 removed the cap; §8.8's only exit is refutation by omission),
# and the panel reports both the full count and what it left out.
MAX_LISTED_ONTOLOGIES = 20

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
/* `height: 1fr` here would size the body to the CONTAINER, clipping a long
   graph instead of overflowing it: the container's virtual size then equals its
   own height, max_scroll_y is 0, and the arrows have nothing to scroll.
   `height: auto` lets the body take its content's height, so the container
   scrolls (measured: a 26-line body in a 20-high container gave max_scroll_y 0
   before, 6 after). */
#ont-body {
    height: auto;
}
/* The REPL source is ONE line to be copied, so it must not wrap: a terminal
   copies wrapped text with the break in it and the paste fails to parse.
   `overflow-x: auto` scrolls it sideways instead (shift+arrows, or the
   scrollbar); `height: auto` keeps it to the lines it actually has. */
#ont-legend {
    height: auto;
    padding: 0 1;
}
#ont-note {
    height: auto;
    padding: 1 1 0 1;
}
#ont-src {
    height: 1;
    width: auto;
    overflow-x: auto;
    text-wrap: nowrap;
    color: $text;
    background: $panel;
    padding: 0 1;
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
/* `height: auto`, not `1fr` — see the note on #ont-body. A reduction sequence
   long enough to overflow would otherwise be clipped rather than scrollable. */
#beta-body {
    height: auto;
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


class OntologyItem(ListItem):
    """One ontology of the current reading — ont(G(t), P), §8.1."""

    def __init__(self, ontology, index: int) -> None:
        n_ptr = len(getattr(ontology, "pointers", {}) or {})
        fired = getattr(ontology, "fired", 0)
        # `fired` counts how often this model's hypotheses actually paid off —
        # a confirmed survivor is a better approximation than a silent one (O8).
        # It counts BOTH rules: rule 1 for a contraction (§8.6) and rule 4 for a
        # reflection (§8.7). The list is ordered by it, descending.
        badge = (f"[green]✓{fired}[/green]" if fired else "[dim]·[/dim]")
        # the reading is a member of its own set: trivially a model of itself,
        # and the one member that can never fire (a reading has no abstractions).
        if getattr(ontology, "is_reading", False):
            badge = "[cyan]≡[/cyan]"
        tag = "  [cyan]the reading itself[/cyan]" if getattr(
            ontology, "is_reading", False) else ""
        # SPELL THE FIRING COUNT OUT, not only as the leading badge: it is what
        # the list is ordered by, and it counts BOTH rules — rule 1 for a
        # contraction (§8.6) and rule 4 for a reflection (§8.7).
        fired_s = (f"[green]fired {fired}×[/green]" if fired
                   else "[dim]fired 0×[/dim]")
        display = (
            f"{badge} [b]{index + 1}.[/b] {esc(str(ontology.term))}"
            f"{tag}  [dim]|P|={n_ptr}[/dim]  {fired_s}"
        )
        super().__init__(Label(display, markup=True))
        self.ontology = ontology
        self.index = index


class OntologyDetailModal(ModalScreen):
    """One ontology's whole term, rendered as a graph.

    TWO VIEWS, toggled with `o`:

      RESOLVED  the term as it stands now — the hypothesis after the reading has
                consumed part of it by reduction.
      ORIGINAL  the shared graph this ontology was PROPOSED as, before the first
                reduction the reading caused (`Ontology.origin`).

    They differ exactly when the reading has fired a rule in this ontology. The
    original is what shows which abstractions the model actually claimed —
    reduction consumes them, so the resolved term alone does not say.
    """

    CSS = ONTOLOGY_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
        Binding("o", "toggle_origin", "Original / resolved"),
    ]

    def __init__(self, g: rdflib.Graph, ontology, index: int) -> None:
        super().__init__()
        self._g = g
        self._o = ontology
        self._index = index
        self.show_origin = False

    # the pre-reduction graph, when there is a distinct one to show
    @property
    def _origin(self):
        o = self._o
        return o.origin if getattr(o, "origin", None) is not None else None

    def compose(self) -> ComposeResult:
        # Both panes are filled by `_refresh`, so the toggle re-renders in place
        # instead of rebuilding the screen.
        with ScrollableContainer(id="ont-container"):
            yield Static("", id="ont-title", markup=True)
            yield Static("", id="ont-body", markup=True)
            # THE REPL SOURCE GETS ITS OWN PANE, and it must not WRAP. The term
            # is one line the reader copies into the REPL, and a terminal copies
            # wrapped text with the break in it — the paste then fails to parse.
            # So this pane scrolls horizontally instead (`#ont-src` in the CSS),
            # keeping the line intact for selection.
            #
            # markup=False as well: the source is not markup, and a label
            # containing '[' would otherwise be eaten as a tag.
            yield Static("", id="ont-src", markup=False)
            # the legend for those one-letter names; wraps freely, unlike the
            # source line above it
            yield Static("", id="ont-legend", markup=True)

    def on_mount(self) -> None:
        self._refresh()

    def action_toggle_origin(self) -> None:
        if self._origin is None:
            self.app.bell()          # nothing reduced: there is no other graph
            return
        self.show_origin = not self.show_origin
        self._refresh()

    def _refresh(self) -> None:
        o = self._o
        ptrs = getattr(o, "pointers", {}) or {}
        fired = getattr(o, "fired", 0)
        origin = self._origin
        showing_origin = self.show_origin and origin is not None
        term = origin if showing_origin else o.term

        claims = collect_edge_claims(self._g, term)
        claims_by_edge: dict[tuple[str, str], list[str]] = {}
        for c in claims:
            claims_by_edge.setdefault((c["subj_iri"], c["obj_iri"]), []).append(
                c["claim_text"])

        if origin is None:
            # Nothing has fired, so the proposal IS the current graph. Say so
            # rather than offering a toggle that would show the same thing.
            what = ("[b]as proposed[/b] [dim]— nothing has reduced here, so this "
                    "is also the original graph[/dim]")
        elif showing_origin:
            what = ("[b]ORIGINAL[/b] [dim]— the graph this ontology was proposed "
                    "as, before the first reduction (o: show resolved)[/dim]")
        else:
            what = ("[b]RESOLVED[/b] [dim]— after the reading reduced it "
                    f"({fired}× ) (o: show ORIGINAL, pre-reduction)[/dim]")

        self.query_one("#ont-title", Static).update(
            f"Ontology {self._index + 1}  [dim](rule fired {fired}×  |  "
            f"|P|={len(ptrs)}  |  Esc close)[/dim]\n{what}"
        )

        lines: list[str] = []
        _render_lam_root(term, claims_by_edge, lines)
        # THE TERM AS REPL SOURCE, under whichever graph is shown. The graph view
        # says what the model is; this says it in a form that can be pasted into
        # the optimal_lambda REPL and reduced there, which is how a reader checks
        # what the ontology actually does.
        #
        # Rendered for BOTH views, since each is a different term: the original is
        # the proposal, the resolved one what the reading left of it.
        lines.append("")
        lines.append("[dim]term (copy into the optimal_lambda REPL):[/dim]")
        self.query_one("#ont-body", Static).update("\n".join(lines))
        self.query_one("#ont-src", Static).update(repl_source(term))
        # THE LEGEND IS WHAT MAKES THE ONE-LETTER TERM READABLE. The source uses
        # single-character names so it stays short enough to read and paste, and
        # without the legend it reduces correctly but says nothing about the
        # graph. Each view has its OWN legend: the two are different terms, so
        # the letters are assigned separately.
        legend = ", ".join(f"{n}={lbl}" for n, lbl in repl_legend(term))
        self.query_one("#ont-legend", Static).update(
            f"[dim]{esc(legend)}[/dim]" if legend else "")


class ReadingOntologiesModal(ModalScreen):
    """The ontologies of the ACTUAL reading — notes/reading_desc §8.

    Each is a model in which the reading is valid: a term WITH abstractions whose
    reduction replays the reading (contraction ↔ R1, reflection ↔ R4). Selecting
    one renders its whole graph.
    """

    CSS = ONTOLOGY_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    def __init__(self, g: rdflib.Graph, ontologies: list, has_reading: bool,
                 stats: dict | None = None) -> None:
        super().__init__()
        self._g = g
        self._ontologies = list(ontologies)
        self._has_reading = has_reading
        self._stats = stats or {}

    def compose(self) -> ComposeResult:
        n = len(self._ontologies)
        st = self._stats
        bits = [f"{k} {st[k]}" for k in ("fired", "silent", "grafted", "refuted",
                                         "dropped") if st.get(k)]
        detail = f"  [dim]({', '.join(bits)})[/dim]" if bits else ""
        with ScrollableContainer(id="ont-container"):
            yield Static(
                f"Ontologies of the current reading: [b]{n}[/b]{detail}"
                f"  [dim]— most rules fired first, then non-ai questions"
                f"  (fired = rule 1 for a contraction + rule 4 for a "
                f"reflection)[/dim]\n"
                f"[dim](Enter: show graph, then o: original pre-reduction graph"
                f"  |  Esc close)[/dim]",
                id="ont-title", markup=True,
            )
            if not self._has_reading:
                yield Static(
                    "[dim]No reading in progress. Start one from an entity, then "
                    "the models in which it is valid are listed here.[/dim]",
                    id="ont-body", markup=True,
                )
                return
            if not self._ontologies:
                # An EMPTY SET IS MEANINGFUL (§8.8) — not an error.
                yield Static(
                    "[b]No valid model.[/b]\n\n"
                    "[dim]No available explicit material can express what this "
                    "reading is doing — it has outrun the abstractions available "
                    "to it. This is a distinguished state, not a failure "
                    "(reading_desc §8.8).\n\n"
                    "An ontology is built from the POSITIVE FORM of saved "
                    "abstractions ('Ask question', key a). Saving more of them "
                    "gives the layer more models to propose.[/dim]",
                    id="ont-body", markup=True,
                )
                return
            # ORDERED BY HOW MANY RULES FIRED, DESCENDING. `fired` counts every
            # firing this ontology has had — rule 1 for a contraction (§8.6) and
            # rule 4 for a reflection (§8.7) alike — so it measures how often the
            # model's hypotheses actually paid off. The most-confirmed survivor
            # is the most interesting one, so it comes first (cf. `cap`, which
            # evicts by the same key).
            #
            # Ties keep the set's own order, which is stable: `sorted` is stable
            # and the reading's mirror is inserted at position 0 by
            # `sync_reading_ontology`, so it leads its tie group. Numbering
            # follows the DISPLAY order, so the label a row shows is the position
            # it occupies.
            #
            # THE READING'S OWN MIRROR THEREFORE COMES LAST: it can never fire
            # (a reading contains no abstractions, I6), so its count is 0. That
            # follows from the ordering rule rather than working against it — it
            # is the one member that proposes nothing — and it stays findable by
            # its "≡ the reading itself" tag.
            # SECOND CRITERION: THE ORIGIN OF THE QUESTIONS USED. Among members
            # with the SAME number of fired rules, one built from a question
            # whose `origin` is not 'ai' ranks higher — somebody wrote that
            # question for a purpose, so it is the more interesting model.
            #
            # Tested on the ORIGIN GRAPH, not the resolved term: rule 1 consumes
            # the abstraction it fires on, so a reduced ontology no longer holds
            # the questions it was built from (see `Ontology.origin`). Asking the
            # resolved term would rank every fired candidate as 'ai'.
            ordered = sorted(
                self._ontologies,
                key=lambda o: (getattr(o, "fired", 0),
                               has_non_ai_question(
                                   getattr(o, "origin_term", o.term))),
                reverse=True)
            # AT MOST 20 ROWS, and the cut is DISPLAY ONLY — the set itself is
            # untouched (O7: nothing is evicted, §8.8's only exit is refutation
            # by omission). A set can run to tens of thousands of members and
            # they are already ranked, so the rows past the first 20 are the
            # least-confirmed models in the list; showing them costs a row each
            # and tells the reader nothing the ranking has not.
            #
            # WHAT IS DROPPED IS SAID, not silently truncated: the title reports
            # the full count, and the note below says how many are not listed.
            shown = ordered[:MAX_LISTED_ONTOLOGIES]
            yield ListView(
                *[OntologyItem(o, i) for i, o in enumerate(shown)],
                id="ont-body",
            )
            if len(ordered) > len(shown):
                rest = len(ordered) - len(shown)
                least = min(getattr(o, "fired", 0) for o in shown)
                yield Static(
                    f"[dim]{rest} further ontolog"
                    f"{'y' if rest == 1 else 'ies'} not listed — the set holds "
                    f"{len(ordered)}, ranked, and these are its first "
                    f"{len(shown)}. The rest fire at most {least} rule"
                    f"{'' if least == 1 else 's'}.[/dim]",
                    id="ont-note", markup=True,
                )

    def on_mount(self) -> None:
        """Focus the LIST, not its scroll container.

        `ScrollableContainer` is focusable and is mounted first, so it takes the
        initial focus and swallows the arrow keys — the list's own cursor never
        moves and Enter has no selected item to act on. Focusing the list makes
        arrows move the cursor and Enter emit ListView.Selected.
        """
        try:
            self.query_one("#ont-body", ListView).focus()
        except Exception:
            pass          # no list: the empty-set or no-reading message is shown

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, OntologyItem):
            self.app.push_screen(
                OntologyDetailModal(self._g, item.ontology, item.index))


class OntologyModal(ModalScreen):
    """Standalone demo of an ontology term — NOT tied to any reading.

    Kept as-is under its own binding; the reading's real ontology set is
    ReadingOntologiesModal above.
    """

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
                f"Ontology demo term  [dim](standalone example — {hint})[/dim]",
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


class OntologyStepItem(ListItem):
    """A step some ONTOLOGY of the reading already contains, but no claim asserts.

    The ontology layer proposes it: some ont(G(t), P) has a subgraph app(ta, tb)
    with actPtr at one side and this entity's type at the other (reading_desc
    §8.1). Following it is an ordinary contraction — the difference is only where
    the step came from, so it is marked and listed after the real claims.

      forward (option 1)  actPtr at ta, [tb] == this entity — the reader MOVES
      reverse (option 2)  actPtr at tb, [ta] == this entity — the reader STAYS
    """

    def __init__(self, node: rdflib.URIRef, label: str, n_onts: int,
                 titles: list[str], reverse: bool = False) -> None:
        arrow = "←" if reverse else "→"
        via = ""
        if titles:
            first = titles[0]
            more = f"  +{len(titles) - 1}" if len(titles) > 1 else ""
            via = f"  [dim italic]{esc(first[:56])}{more}[/dim italic]"
        display = (
            f"[magenta]◈[/magenta] {arrow}  [b]{esc(label)}[/b]"
            f"  [dim]{n_onts} ontolog{'y' if n_onts == 1 else 'ies'}[/dim]{via}"
        )
        super().__init__(Label(display, markup=True))
        self.node = node
        self.label_text = label
        self.reverse = reverse


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


# ── Reflection  (notes/reading_desc §4.2) ────────────────────────────────────

REFLECTION_CSS = """
Screen {
    align: center middle;
}
#rf-container {
    width: 80%;
    height: 80%;
    background: $surface;
    border: thick $error;
    padding: 1 2;
    layout: vertical;
}
#rf-title {
    text-style: bold;
    color: $error;
    padding-bottom: 1;
}
#rf-picked {
    height: 2;
    color: $text-muted;
}
#rf-search {
    height: 3;
    border: tall $error;
    margin-bottom: 1;
}
#rf-results {
    height: 1fr;
    border: tall $panel-lighten-2;
}
"""


class ReflectionModal(ModalScreen):
    """Select the QUESTION entity A, then the ANSWER entity B  (§4.2).

    Both must be ENTITIES — no reading may be chosen. Reflection grafts nothing,
    so A and B are only types cast onto the two occurrences of the shared
    subterm; a reading could contribute only its type, which is nothing an entity
    does not already give.

    Dismisses with (a_iri, a_label, b_iri, b_label), or None if cancelled.
    """

    CSS = REFLECTION_CSS

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, g: rdflib.Graph, stay_label: str) -> None:
        super().__init__()
        self._g = g
        self._stay_label = stay_label
        self._candidates: list[rdflib.URIRef] = []
        self._a: tuple[str, str] | None = None      # (iri, label)

    def compose(self) -> ComposeResult:
        with Vertical(id="rf-container"):
            yield Static("", id="rf-title", markup=True)
            yield Static("", id="rf-picked", markup=True)
            yield Input(placeholder="Search for an entity…", id="rf-search")
            yield ListView(id="rf-results")

    def on_mount(self) -> None:
        self._refresh_title()
        self.query_one("#rf-search", Input).focus()

    def _refresh_title(self) -> None:
        hint = "type to search  |  ↑↓  |  Enter select  |  Esc cancel"
        stage = "QUESTION (A)" if self._a is None else "ANSWER (B)"
        self.query_one("#rf-title", Static).update(
            f"Reflection at [b]{esc(self._stay_label)}[/b]  [dim]({hint})[/dim]\n"
            f"Select the {stage} entity:"
        )
        picked = self.query_one("#rf-picked", Static)
        if self._a is None:
            picked.update(
                "[dim]The user stays where they are; A and B are reflected, "
                "not entered.[/dim]"
            )
        else:
            picked.update(f"question (A): [b]{esc(self._a[1])}[/b]  →  answer (B): …")

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        lv = self.query_one("#rf-results", ListView)
        lv.clear()
        self._candidates = []
        if not query:
            return
        results = search_nodes(self._g, query, limit=8)
        self._candidates = results
        for node in results:
            lv.append(NodeItem(node, node_label(self._g, node)))

    def on_key(self, event: events.Key) -> None:
        if event.key != "down":
            return
        lv = self.query_one("#rf-results", ListView)
        inp = self.query_one("#rf-search", Input)
        if inp.has_focus and self._candidates:
            event.stop()
            event.prevent_default()
            lv.focus()
            lv.call_after_refresh(setattr, lv, "index", 0)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        lv = self.query_one("#rf-results", ListView)
        if self._candidates:
            lv.focus()
            lv.call_after_refresh(setattr, lv, "index", 0)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, NodeItem):
            return
        iri = str(item.node)
        label = node_label(self._g, item.node)
        if self._a is None:
            self._a = (iri, label)
            self._refresh_title()
            inp = self.query_one("#rf-search", Input)
            inp.value = ""
            inp.focus()
            self.query_one("#rf-results", ListView).clear()
            self._candidates = []
            return
        self.dismiss((self._a[0], self._a[1], iri, label))


class PointerItem(ListItem):
    """One live position of the open reading — a member of Pr (§1).

    Shows where the user stays, the reflected cast if any, and the subterm at
    that position. The actPtr is marked.
    """

    def __init__(self, pointer, subterm_label: str, subterm_str: str,
                 is_act: bool) -> None:
        marker = "[b green]▶[/b green]" if is_act else " "
        if pointer.origin == "reflect-left":
            tag = "[magenta]Q[/magenta]"
        elif pointer.origin == "reflect-right":
            tag = "[magenta]A[/magenta]"
        elif pointer.origin == "init":
            tag = "[dim]·[/dim]"
        else:
            tag = "[dim]→[/dim]"
        cast = ""
        if pointer.cast_type:
            cast = f"  [magenta]{esc(local_name(pointer.cast_type))}[/magenta]"
        display = (
            f"{marker} {tag} [b]{esc(subterm_label)}[/b]{cast}"
            f"  [dim]{esc(subterm_str)}[/dim]"
        )
        super().__init__(Label(display, markup=True))
        self.pointer = pointer
        self.pid = pointer.pid


QTREE_CSS = """
Screen {
    align: center middle;
}
#qt-container {
    width: 88%;
    max-height: 80%;
    background: $surface;
    border: thick $success;
    padding: 1 2;
}
#qt-title {
    text-style: bold;
    color: $success;
    padding-bottom: 1;
}
#qt-body {
    height: 1fr;
}
"""


class QuestionNodeItem(ListItem):
    """One node of the question tree — an ENTITY or a question naming a subtype.

    The entity is the MOST GENERAL question of its type (reading_desc §2), so it
    is a row like any other: selecting it is the old behaviour, selecting a
    question below it puts that subtype into the reading instead.
    """

    def __init__(self, iri: str, label: str, depth: int,
                 n_children: int, is_entity: bool,
                 usable: bool = True) -> None:
        pad = "  " * depth
        if is_entity:
            head = f"[b]{esc(label)}[/b]  [dim]— the entity, most general[/dim]"
        else:
            head = f"[yellow]?[/yellow] {esc(label)}"
        if not usable:
            # the caller restricted what may be chosen; say why rather than
            # hiding the row, so the tree still reads as the whole hierarchy.
            head = f"[dim]{esc(label)}[/dim]"
            more = (f"  [dim]→ {n_children} more[/dim]" if n_children else "")
            super().__init__(Label(
                f"{pad}{head}{more}  [dim](the reading does not stand here)[/dim]",
                markup=True))
            self.iri = iri
            self.label_text = label
            self.depth = depth
            self.n_children = n_children
            self.is_entity = is_entity
            self.usable = False
            return
        more = (f"  [dim]→ {n_children} more[/dim]" if n_children else "")
        super().__init__(Label(f"{pad}{head}{more}", markup=True))
        self.usable = True
        self.iri = iri
        self.label_text = label
        self.depth = depth
        self.n_children = n_children
        self.is_entity = is_entity


class QuestionTreeModal(ModalScreen):
    """Choose a type: an entity, or a question naming a subtype of it.

    ONE SELECTOR FOR EVERY PLACE A TYPE IS CHOSEN — the initial step (§3), a
    contraction operand from either claims panel (§4.1), and the parent when a
    new question is created. They differ only in what the caller does with the
    result, so the navigation is learned once.

    → unfolds the questions under the highlighted row (a level of the tree)
    ← folds that row's children away again
    Enter selects the highlighted row and dismisses with its iri

    Dismisses with (iri, label) or None if cancelled.
    """

    CSS = QTREE_CSS

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("right", "unfold", "Unfold questions"),
        Binding("left", "fold", "Fold"),
    ]

    def __init__(self, tree, root_iri: str, root_label: str,
                 purpose: str = "Select a type",
                 bindable: Optional[set] = None) -> None:
        super().__init__()
        self._tree = tree
        self._root = root_iri
        self._root_label = root_label
        self._purpose = purpose
        # when given, rows outside this set are shown DIMMED and unusable — the
        # reading does not stand in them, so binding there is refused anyway.
        self._bindable = bindable
        # which rows are unfolded, by iri; the root starts folded so the panel
        # opens on the entity alone and the tree is entered deliberately.
        self._open: set[str] = set()

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="qt-container"):
            yield Static("", id="qt-title", markup=True)
            yield ListView(id="qt-body")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#qt-body", ListView).focus()

    # -- the visible rows --------------------------------------------------
    def _rows(self) -> list[QuestionNodeItem]:
        """The tree flattened to the rows currently unfolded."""
        out: list[QuestionNodeItem] = []

        def emit(iri: str, label: str, depth: int, is_entity: bool) -> None:
            kids = self._tree.children_of(iri)
            ok = self._bindable is None or iri in self._bindable
            out.append(QuestionNodeItem(iri, label, depth, len(kids),
                                        is_entity, usable=ok))
            if iri in self._open:
                for k in kids:
                    emit(k, self._tree.title_of(k) or k, depth + 1, False)

        emit(self._root, self._root_label, 0, True)
        return out

    def _refresh(self, keep: int = 0) -> None:
        n_q = len(self._tree.subtypes_of(self._root)) - 1
        self.query_one("#qt-title", Static).update(
            f"{esc(self._purpose)}: [b]{esc(self._root_label)}[/b]"
            f"  [dim]({n_q} question{'' if n_q == 1 else 's'} in its tree)[/dim]\n"
            f"[dim]→ unfold questions  |  ← fold  |  Enter select  |  Esc cancel"
            f"[/dim]"
        )
        lv = self.query_one("#qt-body", ListView)
        lv.clear()
        rows = self._rows()
        for r in rows:
            lv.append(r)
        if rows:
            lv.call_after_refresh(setattr, lv, "index",
                                  min(keep, len(rows) - 1))

    # -- navigation --------------------------------------------------------
    def _current(self) -> Optional[QuestionNodeItem]:
        item = self.query_one("#qt-body", ListView).highlighted_child
        return item if isinstance(item, QuestionNodeItem) else None

    def action_unfold(self) -> None:
        it = self._current()
        if it is None or not it.n_children:
            return
        idx = self.query_one("#qt-body", ListView).index or 0
        self._open.add(it.iri)
        self._refresh(keep=idx)

    def action_fold(self) -> None:
        it = self._current()
        if it is None:
            return
        idx = self.query_one("#qt-body", ListView).index or 0
        if it.iri in self._open:
            self._open.discard(it.iri)
            self._refresh(keep=idx)
        else:
            # already folded: step out to the parent, so ← walks up the tree
            parent = self._tree.parent_of(it.iri)
            if parent is not None:
                self._open.discard(parent)
                self._refresh()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        it = event.item
        if isinstance(it, QuestionNodeItem):
            self.dismiss((it.iri, it.label_text))
