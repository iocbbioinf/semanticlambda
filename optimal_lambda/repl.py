"""Interactive REPL for trying lambda terms against the bus reducer.

Run it as either:

    python -m optimal_lambda
    python -m optimal_lambda.repl

Type a lambda term (``\\`` or ``λ`` for abstraction) and it is compiled to a
bus graph, normalized, and read back:

    λ> (\\x.x x) (\\y.y)
    λy.(y)   [fan=1, book=8]

Commands (prefix ``:``):
    :beta  TERM   show the naive normal-order reduction (lambda_term oracle)
    :norm  TERM   force just the bus normal form (default for a bare term)
    :steps        toggle the [fan=…, book=…] interaction counters
    :help         show this help
    :quit / :q    exit  (Ctrl-D / Ctrl-C also quit)
"""

from __future__ import annotations

from .parser import parse, ParseError
from .term import beta_reduce_sequence
from .compile import compile_term
from .normalize import normalize
from .readback import readback


HELP = """\
Enter a lambda term to reduce it with the optimal bus reducer.
  abstraction:  \\x.x   or   λx.x        multi-arg:  \\f x.f x
  application:  f a b   (left-assoc)     grouping:   (…)

Commands:
  :beta  TERM   naive normal-order reduction (the test oracle)
  :norm  TERM   bus normal form (same as entering a bare term)
  :steps        toggle interaction counters
  :help         this message
  :quit, :q     exit"""


def _reduce(src: str, show_steps: bool) -> str:
    """Compile → normalize → readback, returning a display string."""
    term = parse(src)
    g = normalize(compile_term(term))
    out = str(readback(g))
    if show_steps:
        out += f"   [fan={g.fan_interactions}, book={g.book_interactions}]"
    return out


def _beta(src: str) -> str:
    term = parse(src)
    seq = beta_reduce_sequence(term)
    return f"{seq[-1]}   [{len(seq) - 1} beta step(s)]"


def run_line(line: str, state: dict) -> bool:
    """Handle one input line. Returns False if the REPL should exit."""
    line = line.strip()
    if not line:
        return True

    if line in (":quit", ":q", ":exit"):
        return False
    if line in (":help", ":h", "?"):
        print(HELP)
        return True
    if line == ":steps":
        state["steps"] = not state["steps"]
        print(f"interaction counters {'ON' if state['steps'] else 'OFF'}")
        return True

    try:
        if line.startswith(":beta"):
            print(_beta(line[len(":beta"):]))
        elif line.startswith(":norm"):
            print(_reduce(line[len(":norm"):], state["steps"]))
        else:
            print(_reduce(line, state["steps"]))
    except ParseError as ex:
        print(f"parse error: {ex}")
    except RuntimeError as ex:
        print(f"reduction error: {ex}")
    except Exception as ex:  # keep the REPL alive on any reducer bug
        print(f"{type(ex).__name__}: {ex}")
    return True


def main() -> int:
    state = {"steps": True}
    print("optimal_lambda REPL — :help for commands, :quit to exit")
    while True:
        try:
            line = input("λ> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not run_line(line, state):
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
