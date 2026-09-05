# semanticlambda

The terminal app on this branch is the **reading browser** (below). The earlier
Textual KG browser (`app.py`, `widgets.py`, `kg_browser.py`) was removed from
`main` and is preserved on the **`browser`** branch.

The layers it shared with the reading calculus are kept here, because
`notes/reading_desc`, `notes/reading_alg` and the `reading`/`ontology` skills
cite them as normative: `term_utils` (typing, `_term_type`), `reading_state`
(the pointer set), `ontology_state` (the §8 ontology layer), `kg_store`
(persistence and the RDF graph) and `question_tree`.

## Optimal lambda reduction (`optimal_lambda`)

The `optimal_lambda/` package implements optimal lambda reduction via sharing
graphs — the bus-of-wires formulation of Gonthier, Abadi & Lévy, *The Geometry
of Optimal Lambda Reduction* (POPL 1992). It compiles a lambda term to a bus
graph, normalizes it with the six Figure-2 interaction rules, and reads the
result back to a term.

### Interactive REPL

Try terms interactively with the built-in REPL:

```
python -m optimal_lambda          # or: python -m optimal_lambda.repl
```

Type a lambda term — use `\` or `λ` for abstraction, juxtaposition for
application (left-associative), and parentheses for grouping. The term is
reduced by the bus reducer, and the `[fan=…, book=…]` interaction counters are
shown (fan = R1/R4 "beta" work; book = R2/R3/R5/R6 bookkeeping):

```
optimal_lambda REPL — :help for commands, :quit to exit
λ> (\x.x x) (\y.y)
λy.(y)   [fan=1, book=8]
λ> \f x.f (f x)
λf.(λx.(f · (f · x)))   [fan=0, book=0]
```

Combinator names like `S`/`K` are treated as free variables — write them out
as lambdas (e.g. `(\x y.x) a b`) to see them reduce.

Commands:

| Command       | Effect                                                  |
| ------------- | ------------------------------------------------------- |
| `:beta TERM`  | naive normal-order reduction (the test oracle)          |
| `:norm TERM`  | bus normal form (same as entering a bare term)          |
| `:steps`      | toggle the `[fan=…, book=…]` interaction counters       |
| `:help`       | show help                                               |
| `:quit`, `:q` | exit (Ctrl-D / Ctrl-C also quit)                        |

### Library use

```python
from optimal_lambda import parse, optimal_normal_form

print(optimal_normal_form(parse(r"(\x.x x) (\y.y)")))   # -> λy.(y)
```

### Tests

```
.venv/bin/python tests/test_sharing_graph.py    # reducer: rules + round-trip + oracle
.venv/bin/python tests/test_lambda_parser.py    # parser
```

## Reading browser (`reading_browser.py`)

A terminal app in the shape of Claude Code: you put a query, the query is
delegated to Claude Code (`claude -p`), and every choice you make is a **reading
step** applied to `R = (G(t), Pr)` — the sharing graph of a lambda term without
abstractions (`notes/reading_desc`, `notes/reading_alg`).

```
./run_reading_browser.sh              # or: python reading_browser.py
python reading_browser.py --model opus
```

Entities here are **not** KG nodes: they are proposed freely from the query and
become the **variables** of the term. Entities are reused across steps, and a
reused entity is *one node* of the sharing graph.

### The three interaction steps

Each one is a reading step, and the app renders which is in play:

| | when | reading step | effect |
| --- | --- | --- | --- |
| **A** | an entity can be understood several ways | contraction, **option 1** | `app(t1,t2)`; the reader **moves** to B |
| **B** | a relation can be understood several ways | **reflection** | a sharing fan-in over `t`; the reading **forks**, `\|Pr\|` grows |
| **C** | several ways this place was **reached** | contraction, **option 2** | `app(t1,t2)`; the reader **stays** at B |

An option of kind A may also be an **already created reading** — a closed
reading is what the user answers *with* (never what they ask *from*).

When no interaction step remains at any pointer of the current reading, the
driver closes it and opens a new reading from an entity that has none yet.

`resume` (or Ctrl-C) saves the initial question and all its readings to
`data/browser_sessions.json`; `term` shows the term and its pointer set.

### Tests

```
.venv/bin/python tests/test_reading_session.py   # the three steps, against a stub delegate
```
