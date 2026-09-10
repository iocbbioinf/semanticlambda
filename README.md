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
python reading_browser.py --per-step  # skip the clarification phase (older mode)
```

### The clarification phase (`clarify_plan.py`)

The **first phase** of reading a query is not answering it: it is settling **what
was asked**. Each interaction step resolves one ambiguity in the user's own
words, and the entities of the reading are the ways those words can be
understood — so the reading `R` built here *is* the disambiguated query.

**Phase 0 — the seeds.** Before any interaction the query is analysed into its
**unclear points**, and each is mapped to an entity. Those entities are the
**seeds**, and the set is *fixed*: one reading per seed, exhausted in turn
(§3's "open on a type A"). A point that emerges later, while reading, maps to
an entity without becoming a seed — often to an entity a seed also maps to, in
which case it is **one node reached two ways** (§1). So the number of readings
a query can yield is decided here. There is no entity for "the query as a
whole": the query *is* its seeds and their combination. **If the query has no
unclear point, no interaction starts at all** — the app resumes at once rather
than inventing a question.

Each step is then one point of that seed put back to the user:

```
  in your query: “experimentally shown to interact”

  What counts as qualifying experimental evidence of interaction?
    1  binding assay only
       Narrowest, most literal reading of 'interact with'
    2  functional activity
       Some readers mean pharmacological effect, not mere affinity
    3  any experimental evidence
       Broadest reading, includes structural/co-crystal data too
```

The three kinds are the three reading steps unchanged: **A** the quoted words
themselves read several ways (contraction option 1), **B** how two parts of the
query bear on each other (reflection), **C** which vantage the current place
should be taken under (contraction option 2). The calculus needed no new
operation — only a different answer to *what an entity denotes*.

**One call per step, one model.** Each call asks for exactly one point, from
where the user now stands. An earlier version batched all the points into one
call — cheapest in total, and much worse to use: latency tracks OUTPUT SIZE
because generation is serial, so six points with their options was ~2000 output
tokens and **36s of silence** before the first question, where one point is ~420
tokens and **~7s**. Asking one at a time costs the same per step and shows the
first question sooner. It also asks a better question, since a point planned
before the user chose anything has its options written in ignorance of that
choice — it cannot ask "given that you meant binding affinity, does the BBB
condition mean X or Y". Nothing is *invalidated* by a choice (a point's
candidates are a property of the query text, which is why step C needs no
knowledge of the path taken) — the pre-planned version is simply ranked and
phrased for a reader who has not yet decided.

**A reading keeps what was asked.** Alongside the term and the calculus log
(`[A] contraction opt.1 — moved → X`), every step records an `Interaction`: the
words of the query at issue, the question put to the user, and the answer they
chose. The term says what was *built* and the log says by which reading steps;
only this says what was *asked*, which is what makes a saved reading legible
later rather than merely replayable. It is shown in the `t` view and in the
resume summary, and persisted with the session:

```
  · molecules — molecules that pass the blood-br  3 steps
      · molecules   → “molecules” read narrowly
      · pass        → “pass” restricts what follows — pass restricts (question) · what pass restricts (answer)
      · blood-brain → taking “blood-brain” as strictly as the rest
```

A reflection keeps **both** sides of its pair, since the option label alone
would lose which entity asked and which answered.

**The readings combine.** Each closed reading settled one unclear point, so
they are applied to one another, left-associatively, to assemble one reading of
the whole query:

```
  R1, R2, R3   ->   Rnew = app(app(R1, R2), R3)
```

That is contraction-shaped throughout (§3.1 admits a **closed reading** as an
option-1 operand — what the user answers *with*), and by §2's `[app(t1,t2)] ==
[t2]` the result carries the type of the **last** reading combined.

**Questions are automatic.** There is no `a` command: a question is not the
user's to ask. On `resume` the readings are combined (`Rnew = app(R1, R)`, the
open reading last), and if any seed was never exhausted — a point nobody
settled — the result is abstracted over those points:

```
  question = lam a1. ... lam an. Rnew
```

so the question is a reading of the query still **parametric in what remains
unclarified**. It is a proper §2 question whenever the seed's entity occurs in
`Rnew`, which happens exactly when a point emerging under *another* seed mapped
to it; where it does not occur it is contracted in first, which is §2's own
recipe ("read the body, contract the asked material in, then bind that
occurrence"). Each binder is recorded as its own saved question, built
**innermost first**, because `term_utils.check_question` requires every nested
abstraction to be a saved question — which is also §2's question tree, each
binder naming a subtype of the one below it.

Labels are **names**, at most four words, with the meaning in a required
one-clause gloss: labels print inline in the term, in pointer lines and in
option rows, so a sentence there wrecks the display. That is enforced in the
schema, at the decode boundary, and in `entity_store.StoredEntity` — the last of
those being the one that matters, since a label saved long is handed back to
every later run by `resolve`.

### `--verbose`

**The default rendering is only the interaction.** The words of your query being
clarified, the question, the answers, `r` — and, once a step B has split the
reading, switching between the contexts it opened.

```
  in your query: “aspirin: what it covers”

  how should “aspirin: what it covers” be understood?
    1  “aspirin: what it covers” read narrowly
       the strictest reading of those words
    2  “aspirin: what it covers” read broadly
       the most permissive reading
    r  resume (save and stop)
```

No header, no backend line, no entity count, no up-front list of the unclear
points, no confirmation of the step just applied, no save report. `r` renders
**the reading** the interaction built — the readings of the query, what was
asked and answered in each, the assembled term, and whatever was left
unclarified — and that render is the whole of resuming.

**The frame of each question is rendered** — which reading it belongs to, and
which context it is asked in — because both change what the question means:

```
  ◆ now reading: molecules

  context: retrieve-how-strictly-restricts  · 2 open, p to switch
```

The reading line appears whenever one opens. `Pr` grows monotonically **over a
reading** (§4, I2) — but only over one: §3 opens a reading with exactly one
pointer, and closing collapses the pointer set to the root, so each seed's
reading starts again at |Pr| = 1 and `p` correctly disappears with the contexts
that belonged to the reading that closed. Without the reading line a fresh
reading's first question reads as though it still belonged to the split one — a
missing `p` with two contexts apparently still open.

**Contexts are rendered**, since a step B (reflection) makes two independent
occurrences and which one the reading continues from is a choice, not a trace of
one. `p` is offered at a menu **if and only if |Pr| > 1** in the reading being
read right then. The split is announced and the other context offered
immediately:

```
  the reading split — you are in context restricts; the other is restricted
  continue in the other context instead? [y/N] y
  ✓ now in context restricted
```

```
  where do you want to continue?
    1  ▸ aspirin, narrowly  context: restricts
    2    aspirin, narrowly  context: restricted
    c  cancel
```

The picker is also offered when the move is **forced** — no step left at this
pointer, or `s` — and more than one context is still open: `mark_exhausted`
would otherwise take the first one and carry on, choosing for the user. Nothing
is announced about where actPtr lands, though: the place it moves to may have
nothing either, in which case the reading closes, and a line promising to
continue there would be false by the next step. Where the user stands is
rendered on the question actually asked.

`s`, `o` and `q` still work; they are simply not listed, since pressing one is
you asking for something rather than the app putting it in front of you.

`--verbose` (`-v`) adds everything else back: the header and command list, the
flow narration (which reading is being opened, which step was applied), the save
report, and the calculus — the term as it grows, the pointer set `Pr`, which
reading step each choice performs (`contraction · option 1`), where you are
standing, the `t` (whole reading) and `e` (entity store) views, and — under
`--claude` — what each delegation cost:

```
  /time  next step prepared in 7.0s  · delegated to claude -p
  /cost  this step $0.0574  ·  session $0.1147  ·  took 7.0s (api 7.0s + startup 0.0s)
```

`/time` says how long preparing the step took and whether it cost a delegation —
worth seeing, since a step served without a call should be instant and one that
waits on `claude -p` should say so rather than look like the app being slow.
`/cost` reports the `total_cost_usd` that call's envelope returned, the running
total for the session, and the wall/api split (the gap is process startup).

Both are printed only when a delegation actually happened: opening the
`t`/`e`/`o` views or switching context asks the delegate nothing, and reprinting
the previous call's figures would read as if the step had cost them again. Under
the mock every counter stays `0.0` and the lines are omitted.

The context switcher `p` is rendered in both modes. Its picker names each place
by the entity and the context it carries (`aspirin as an agent  context:
its-mechanism`) and confirms the switch by that context; `--verbose` names them
by pointer id and aux port (`p1  aspirin  reflected as its-mechanism · grey ·
left-up`) and adds `|Pr|`. The reading is built identically either way — the
flag only decides how much is narrated.

### The two delegates

`reading_mock.MockAgent` is the **default**: no Claude call, no cost, no CLI
needed. Its entities come from the query's own words plus a stock vocabulary, so
a reading is nonsense *as knowledge* — but well formed *as a reading*, and it
cycles A → B → C so all three step kinds are reached in a short session. It is
deterministic per query, so a run repeats exactly.

`reading_agent.ReadingAgent` (`--claude`) delegates to `claude -p` with a JSON
schema. Real runs cost roughly $0.10–0.25 per session on Sonnet; `--verbose`
shows the running cost step by step, and the resume summary prints the session
total either way.

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
