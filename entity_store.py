"""The entity store — one node per entity, however it is named.

Entities are the VARIABLES of a reading, and an entity the user reuses must be
ONE node of the sharing graph (reading_desc §1). The registry in `reading_state`
guarantees that for one id; this module is what makes two *namings* of the same
thing arrive at one id in the first place.

WHY IT IS NEEDED. The delegate invents the names. Asked twice about the same
thing it may answer "COX enzymes", then "the COX enzymes", then "COX enzyme" —
three ids, three variables, three nodes, and the sharing that should have been
there is silently lost. Nothing downstream can recover it: by the time a term is
built, two variables with different iris are simply two entities.

WHAT IT DOES NOT DO. It merges NAMES, not meanings. "COX-1" and "COX-2" differ by
one character and are genuinely different entities, so the matching is
deliberately conservative: normalise away only what carries no meaning (case,
punctuation, articles, a trailing plural), and otherwise keep them apart. A false
merge destroys a distinction the user drew; a missed merge only fails to share.
When in doubt, do not merge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Words that carry no identity of their own. Dropped only when they are not the
# whole name — "the case" keeps its article, since "case" alone is different.
_NOISE = {"the", "a", "an", "of", "its", "their", "this", "that"}


def slugify(text: str) -> str:
    """The id form of a name: lowercase, alphanumeric, single hyphens."""
    s = "".join(c if (c.isalnum() or c in " -_") else " " for c in text.lower())
    return "-".join(s.replace("_", " ").replace("-", " ").split())[:64] or "thing"


def _singular(word: str) -> str:
    """A crude, deliberately timid de-pluraliser.

    Only the endings that are unambiguous in practice. `-ss` (class, process) and
    short words are left alone; nothing here should turn one entity into another.
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("ses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def match_key(text: str) -> str:
    """The key two names share when they name the same thing.

    Normalises away what carries no identity — case, punctuation, articles, a
    trailing plural — and nothing else. Two names with the same key are treated
    as one entity; anything else stays separate.
    """
    words = [w for w in slugify(text).split("-") if w]
    kept = [w for w in words if w not in _NOISE] or words
    return "-".join(_singular(w) for w in kept)


def search_score(text: str, query: str) -> float:
    """How well `text` answers `query` — 0 for no match.

    The scoring the old KG browser used (`kg_store.search_nodes`): split the
    query into words and count how many occur in the text, so typing more words
    narrows rather than excludes. Two additions that matter when the candidates
    are short entity labels rather than KG nodes:

      a PREFIX hit outranks a hit in the middle, so typing "cox" puts
      "COX enzymes" above "reduced COX activity";
      an EXACT label wins outright.
    """
    words = [w for w in query.lower().split() if w]
    if not words:
        return 0.0
    hay = text.lower()
    parts = hay.replace("-", " ").split()

    def hit(w: str) -> bool:
        # A SHORT word must match at a word boundary. Free substring matching is
        # right for long words ("infl" -> "inflammation") but on short entity
        # labels it is mostly noise: "as" would otherwise hit "case", so typing
        # "aspirin as" would list every entity containing "case".
        if len(w) <= 3:
            return any(p == w or p.startswith(w) for p in parts)
        return w in hay

    hits = sum(1 for w in words if hit(w))
    if hits == 0:
        return 0.0
    score = float(hits)
    if hay == query.lower().strip():
        score += 10.0
    elif hay.startswith(words[0]):
        score += 2.0
    elif any(part.startswith(words[0]) for part in hay.split()):
        score += 1.0
    return score


@dataclass
class StoredEntity:
    """One entity, with every name it has been proposed under."""
    iri: str
    label: str
    gloss: str = ""
    aliases: list[str] = field(default_factory=list)
    # how many times this entity has been reached, for the manage view
    uses: int = 0

    def note_alias(self, name: str) -> None:
        if name and name != self.label and name not in self.aliases:
            self.aliases.append(name)


class EntityStore:
    """Names in, one entity out.

    The store is authoritative for identity: `resolve` either returns an entity
    already held — because the incoming name matches one — or mints a new one.
    Callers never construct an `Entity` themselves.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, StoredEntity] = {}
        self._by_iri: dict[str, StoredEntity] = {}
        self.merges: list[tuple[str, str]] = []   # (incoming name, kept label)

    # ── resolution ────────────────────────────────────────────────────────

    def resolve(self, raw_id: str, label: str = "", gloss: str = "") -> StoredEntity:
        """The entity for this name — an existing one when it matches.

        `raw_id` is what the delegate offered as the id; `label` its human name.
        Both are considered, because the delegate is inconsistent about which one
        carries the distinguishing words.
        """
        raw = (raw_id or label or "").strip()
        if raw.lower().startswith("local:"):
            raw = raw.split(":", 1)[-1]
        name = (label or raw).strip()
        if not raw:
            raw = name

        # An exact iri hit is the common case and needs no matching.
        iri = f"local:{slugify(raw)}"
        hit = self._by_iri.get(iri)
        if hit is not None:
            hit.uses += 1
            hit.note_alias(name)
            return hit

        # Otherwise: does some stored entity share this name's key?
        key = match_key(raw) or match_key(name)
        hit = self._by_key.get(key)
        if hit is None and name:
            hit = self._by_key.get(match_key(name))
        if hit is not None:
            hit.uses += 1
            hit.note_alias(name)
            self.merges.append((name or raw, hit.label))
            # The alternative spelling must also resolve directly next time.
            self._by_iri.setdefault(iri, hit)
            return hit

        ent = StoredEntity(iri=iri, label=name or raw, gloss=gloss, uses=1)
        self._by_key[key] = ent
        self._by_iri[iri] = ent
        return ent

    # ── access ────────────────────────────────────────────────────────────

    def get(self, iri: str) -> Optional[StoredEntity]:
        return self._by_iri.get(iri)

    def all(self) -> list[StoredEntity]:
        """Distinct entities, most used first."""
        seen, out = set(), []
        for e in self._by_iri.values():
            if id(e) not in seen:
                seen.add(id(e))
                out.append(e)
        return sorted(out, key=lambda e: (-e.uses, e.label.lower()))

    def search(self, query: str, limit: int = 12) -> list[StoredEntity]:
        """Entities matching `query`, best first — the type-to-filter lookup.

        Aliases are searched too, so an entity found under one name is reachable
        by another it was proposed under.
        """
        if not query.strip():
            return self.all()[:limit]
        scored = []
        for e in self.all():
            best = max([search_score(e.label, query)]
                       + [search_score(a, query) for a in e.aliases]
                       + [search_score(e.iri.split(":", 1)[-1].replace("-", " "),
                                       query)])
            if best > 0:
                scored.append((best, e))
        scored.sort(key=lambda x: (-x[0], x[1].label.lower()))
        return [e for _s, e in scored[:limit]]

    def __len__(self) -> int:
        return len(self.all())

    def take_merges(self) -> list[tuple[str, str]]:
        """Merges since the last call — the app reports these to the user."""
        out, self.merges = self.merges, []
        return out
