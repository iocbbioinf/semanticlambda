from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class LamVar:
    """A variable (leaf) identified by its IRI."""
    iri: str
    label: str

    def to_dict(self) -> dict:
        return {"type": "var", "iri": self.iri, "label": self.label}

    @staticmethod
    def from_dict(d: dict) -> "LamVar":
        return LamVar(iri=d["iri"], label=d["label"])

    def __str__(self) -> str:
        return self.label


@dataclass
class LamApp:
    """Application: (func arg)."""
    func: "LamTerm"
    arg: "LamTerm"

    def to_dict(self) -> dict:
        return {"type": "app", "func": lam_to_dict(self.func), "arg": lam_to_dict(self.arg)}

    @staticmethod
    def from_dict(d: dict) -> "LamApp":
        return LamApp(func=lam_from_dict(d["func"]), arg=lam_from_dict(d["arg"]))

    def __str__(self) -> str:
        f = str(self.func)
        a = str(self.arg)
        # wrap in parens unless already atomic
        f_s = f if isinstance(self.func, LamVar) else f"({f})"
        a_s = a if isinstance(self.arg, LamVar) else f"({a})"
        return f"{f_s} · {a_s}"


@dataclass
class LamAbs:
    """Abstraction: λvar.body"""
    var: LamVar
    body: "LamTerm"

    def to_dict(self) -> dict:
        return {"type": "abs", "var": self.var.to_dict(), "body": lam_to_dict(self.body)}

    @staticmethod
    def from_dict(d: dict) -> "LamAbs":
        return LamAbs(var=LamVar.from_dict(d["var"]), body=lam_from_dict(d["body"]))

    def __str__(self) -> str:
        return f"λ{self.var}.({self.body})"


LamTerm = Union[LamVar, LamApp, LamAbs]


def lam_to_dict(t: LamTerm) -> dict:
    return t.to_dict()


def lam_from_dict(d: dict) -> LamTerm:
    kind = d["type"]
    if kind == "var":
        return LamVar.from_dict(d)
    if kind == "app":
        return LamApp.from_dict(d)
    if kind == "abs":
        return LamAbs.from_dict(d)
    raise ValueError(f"Unknown lambda term type: {kind}")


def lam_subst(term: LamTerm, var: LamVar, value: LamTerm) -> LamTerm:
    """Substitute value for all free occurrences of var (matched by IRI) in term."""
    if isinstance(term, LamVar):
        return value if term.iri == var.iri else term
    if isinstance(term, LamApp):
        return LamApp(func=lam_subst(term.func, var, value),
                      arg=lam_subst(term.arg, var, value))
    if isinstance(term, LamAbs):
        if term.var.iri == var.iri:
            return term  # bound variable shadows substitution
        return LamAbs(var=term.var, body=lam_subst(term.body, var, value))
    return term


def beta_step(term: LamTerm) -> Optional[LamTerm]:
    """Perform one outermost-leftmost beta reduction step. Returns None if no redex."""
    if isinstance(term, LamApp):
        if isinstance(term.func, LamAbs):
            # Redex found: (λv.body) arg → body[v := arg]
            return lam_subst(term.func.body, term.func.var, term.arg)
        # Outermost-leftmost: try func first, then arg
        reduced_func = beta_step(term.func)
        if reduced_func is not None:
            return LamApp(func=reduced_func, arg=term.arg)
        reduced_arg = beta_step(term.arg)
        if reduced_arg is not None:
            return LamApp(func=term.func, arg=reduced_arg)
    if isinstance(term, LamAbs):
        reduced_body = beta_step(term.body)
        if reduced_body is not None:
            return LamAbs(var=term.var, body=reduced_body)
    return None


def beta_reduce_sequence(term: LamTerm, max_steps: int = 32) -> list[LamTerm]:
    """Return [term, step1, step2, ...] until normal form or max_steps reached."""
    steps = [term]
    current = term
    for _ in range(max_steps):
        nxt = beta_step(current)
        if nxt is None:
            break
        steps.append(nxt)
        current = nxt
    return steps
