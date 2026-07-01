# semanticlambda

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
