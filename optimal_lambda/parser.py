"""Parser for plain lambda-calculus source text into LamTerm ASTs.

Grammar (standard, left-assoc application binds tighter than abstraction):

    term   := abs | app
    abs    := ('λ' | '\\') NAME+ '.' term
    app    := atom+
    atom   := NAME | '(' term ')'

Examples: "λx.x", "\\x.x x", "λf x.f (f x)", "(λx.x) y"

Each parsed occurrence of a variable name gets iri="iri:" + name, matching
the convention used in tests/test_sharing_graph.py.
"""

from __future__ import annotations

import re

from .term import LamTerm, LamVar, LamApp, LamAbs

_TOKEN_RE = re.compile(r"[λ\\.()]|[^\sλ\\.()]+")


class ParseError(ValueError):
    pass


def _tokenize(s: str) -> list[str]:
    return _TOKEN_RE.findall(s)


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> str:
        tok = self._peek()
        if tok is None:
            raise ParseError("unexpected end of input")
        self.pos += 1
        return tok

    def _expect(self, tok: str) -> None:
        got = self._next()
        if got != tok:
            raise ParseError(f"expected {tok!r}, got {got!r}")

    def parse_term(self) -> LamTerm:
        if self._peek() in ("λ", "\\"):
            return self._parse_abs()
        return self._parse_app()

    def _parse_abs(self) -> LamTerm:
        self._next()  # consume λ/\
        names = []
        while self._peek() not in (".", None):
            names.append(self._next())
        if not names:
            raise ParseError("abstraction needs at least one bound variable")
        self._expect(".")
        body = self.parse_term()
        for name in reversed(names):
            body = LamAbs(var=LamVar(iri="iri:" + name, label=name), body=body)
        return body

    def _parse_app(self) -> LamTerm:
        terms = [self._parse_atom()]
        while self._peek() not in (None, ")", "."):
            terms.append(self._parse_atom())
        term = terms[0]
        for arg in terms[1:]:
            term = LamApp(func=term, arg=arg)
        return term

    def _parse_atom(self) -> LamTerm:
        tok = self._peek()
        if tok == "(":
            self._next()
            inner = self.parse_term()
            self._expect(")")
            return inner
        if tok in ("λ", "\\"):
            return self._parse_abs()
        if tok in (None, ")", "."):
            raise ParseError(f"unexpected token {tok!r}")
        name = self._next()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", name):
            raise ParseError(f"invalid variable name {name!r}")
        return LamVar(iri="iri:" + name, label=name)


def parse(s: str) -> LamTerm:
    """Parse a lambda-calculus term from source text into a LamTerm AST."""
    tokens = _tokenize(s)
    if not tokens:
        raise ParseError("empty input")
    parser = _Parser(tokens)
    term = parser.parse_term()
    if parser._peek() is not None:
        raise ParseError(f"unexpected trailing token {parser._peek()!r}")
    return term
