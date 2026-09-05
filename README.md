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

A terminal app in the shape of Claude Code: you put a query, a **delegate**
proposes the entities and options, and every choice you make is a **reading
step** applied to `R = (G(t), Pr)` — the sharing graph of a lambda term without
abstractions (`notes/reading_desc`, `notes/reading_alg`).

```
./run_reading_browser.sh              # mock backend — free, offline, instant
python reading_browser.py --verbose   # show the calculus behind each choice
python reading_browser.py --claude    # delegate to Claude Code for real
python reading_browser.py --claude --model opus
python reading_browser.py --seed 7    # vary the mock
```

### `--verbose`

By default the app shows only what you are choosing between: the delegate's
question and its options. `--verbose` (`-v`) adds the calculus — the term as it
grows, the pointer set `Pr`, which reading step each choice performs
(`contraction · option 1`), where you are standing, and the `t` (whole reading)
and `e` (entity store) views.

The context switcher `p` is offered in both modes, since choosing which context
to continue in is a real choice rather than a trace of one; quietly it names each
place by the entity and the context it carries (`aspirin as an agent  context:
its-mechanism`), and under `--verbose` by its pointer id and aux port. The reading is built identically either way — the flag only
decides how much is narrated.

### The two delegates

`reading_mock.MockAgent` is the **default**: no Claude call, no cost, no CLI
needed. Its entities come from the query's own words plus a stock vocabulary, so
a reading is nonsense *as knowledge* — but well formed *as a reading*, and it
cycles A → B → C so all three step kinds are reached in a short session. It is
deterministic per query, so a run repeats exactly.

`reading_agent.ReadingAgent` (`--claude`) delegates to `claude -p` with a JSON
schema. Real runs cost roughly $0.10–0.25 per session on Sonnet.

Entities here are **not** KG nodes: they are proposed freely from the query and
become the **variables** of the term. Entities are reused across steps, and a
reused entity is *one node* of the sharing graph.

They **persist** in `data/entities.json`, loaded on start and written on
`resume`, so an entity named in one session is not re-invented under a fresh iri
in the next — the sharing survives between runs as well as within one. Aliases
and use counts persist with them, so a later session naming the same thing
differently still lands on the same entity.

### The three interaction steps

Each one is a reading step, and the app renders which is in play:

| | when | reading step | effect |
| --- | --- | --- | --- |
| **A** | an entity can be understood several ways | contraction, **option 1** | `app(t1,t2)`; the reader **moves** to B |
| **B** | a relation can be understood several ways | **reflection** | a sharing fan-in over `t`; the reading **splits into two contexts**, `\|Pr\|` grows |
| **C** | several ways this place was **reached** | contraction, **option 2** | `app(t1,t2)`; the reader **stays** at B |

An option of kind A may also be an **already created reading** — a closed
reading is what the user answers *with* (never what they ask *from*).

### The entity store

Entities are the term's variables, and an entity the user reuses must be **one
node** of the sharing graph. The delegate, though, invents the names: asked twice
about one thing it may answer "COX enzymes", then "the COX enzymes", then "COX
enzyme" — three ids, three variables, and the sharing silently lost.

`entity_store.EntityStore` is therefore authoritative for identity. Every
proposed entity passes through it, and a name that matches one already held comes
back as that entity, so it lands as the same node. Matching normalises away only
what carries no meaning — case, punctuation, articles, a trailing plural — and is
otherwise **conservative**: `COX-1` and `COX-2` stay apart, as do `aspirin` and
`aspirin resistance`. A false merge would destroy a distinction the user drew; a
missed merge only fails to share.

Merges are reported as they happen (`↺ “the COX enzymes” is COX enzymes`), and
`e` shows the store: each entity, how often it has been reached, its aliases, and
which are in the reading being built.

### Asking a question

At every step `a` asks a question: you write the text, then choose the entity it
is about. A question is an **abstraction** `λa.t` (`notes/reading_desc` §2) — the
reading built so far is the **body**, the chosen entity the **binder** — and it
names a **subtype** of that entity's type, so the questions of an entity form a
tree under it with the entity itself as the most general question.

You commit the question text first; then a **type-ahead picker** opens, working
as the KG browser's search box did (`incremental_select.py`): the list below
redraws on **every keystroke**, `↑`/`↓` move the highlight, Enter selects, Esc
cancels. Words match independently, so more words narrow rather than exclude.

What it lists is entities **and questions already asked** — a question names a
subtype of its entity's type, so asking a further question of one is what makes
questions form a tree; those rows are marked `?`. Entities already in the reading
come first and are marked, since asking about one of them needs no contraction.
Aliases are searched too, so an entity is reachable by any name it was proposed
under.

When stdin is not a terminal (a pipe, a scripted test) the picker falls back to
a plain type-then-number prompt. One that is not yet in the reading
is **contracted in first**, because `term_utils.check_question` refuses an
abstraction whose bound variable does not occur free: the question would be about
nothing, and since `[G(λa.t)] == [a]` enrichment would offer it as a candidate of
a type it can never place. That is exactly how §2 describes building a question —
read the body, contract the asked material in, then bind that occurrence.

Asking closes the reading that became the body and opens a new one **standing in
the question**, whose seed carries the minted subtype iri and renders the
question's title. On `resume` the questions are written to the shared question
store (`data/lambda_terms.json`, via `term_utils.append_lambda_term`) — the only
way material reaches enrichment — with an empty graph, since these entities are
not KG nodes and so carry no claims.

### Ontologies

Every reading carries a **set of ontologies** (`notes/reading_desc` §8): the
models in which it is valid — each one a hypothesis about what the user is doing.
The set is maintained at every interaction step, shrinking by **refutation** and
growing by **enrichment**, so the survivors are better approximations as the
reading continues.

Validity means **reduction replays the reading**: a contraction is rule 1 firing,
a reflection is rule 4. The reading layer itself never reduces — only this one
does. The reading is a member of its own set (§8.1b), qualifying trivially and
never firing, so the set is never empty; a set holding *only* the reading means
no available abstraction accounts for what the user is doing, which the app says
rather than treats as an error.

`o` lists the set; a number opens one ontology in **both forms**:

| | |
| --- | --- |
| **original** | the graph as **proposed**, before any reduction — the only form that shows which questions the model claimed, since rule 1 *consumes* the abstraction it fires on |
| **reduced** | what is left after the firings the reading approved |

Ontologies are built from the **positive forms** of saved questions (`λa.t`), so
the set is only non-trivially populated once questions exist to enrich with —
which is what `a` creates.

### Contexts: choosing where you continue

Reflection is the only step that grows `Pr`, and its two pointers are
**independent positions** over one shared subject — contracting at one leaves the
other alone. Each is a **context**: the same subject read under a different
cast. So after a **B** step the app asks which context to continue in, and `p`
re-opens that choice at any time, listing each place with the entity it stands
at, the context (cast) it carries, and — under `--verbose` — which aux port it is
(grey/left-up or black/right-up). Continuing somewhere again also clears its
"exhausted" mark.

When no interaction step remains at any pointer of the current reading, the
driver closes it and opens a new reading from an entity that has none yet.

`resume` (or Ctrl-C) saves the initial question and all its readings to
`data/browser_sessions.json`; `term` shows the term and its pointer set.

### Tests

```
.venv/bin/python tests/test_reading_session.py   # the three steps, against a stub delegate
.venv/bin/python tests/test_entity_store.py      # entity identity: what merges, what must not
.venv/bin/python tests/test_ask_question.py      # questions as abstractions, and the store
.venv/bin/python tests/test_incremental_select.py # the type-ahead picker, driven under a pty
.venv/bin/python tests/test_ontologies.py        # the §8 layer: models, firing, both forms
```
