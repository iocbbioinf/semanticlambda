"""Entity mapping — nearest vector among the entities we hold.

An entity is a VARIABLE of an interaction, and an entity the user reuses must be
ONE node of the sharing graph. So two NAMINGS of the same thing have to arrive at
one id: asked twice about the same thing a delegate may answer "COX enzymes",
then "the COX enzymes", then "COX enzyme" — three ids, three variables, three
nodes, and the sharing that should have been there is silently lost.

WHAT IS NEW AGAINST `entity_store.py`. The old store matched NAMES: normalise
away case, punctuation, articles and a trailing plural, then compare. It says so
itself — "It merges NAMES, not meanings" — and that is exactly its limit.
"aspirin" and "acetylsalicylic acid" are one thing under every naming and share
no characters. This module maps by MEANING instead: embed the name and its
gloss, and take the nearest vector already held.

THE BACKEND IS AN INTERFACE (`VectorBackend`). Production is Qdrant with the
hybrid scheme the sparql-llm project uses — a dense vector for meaning and a
sparse one for the literal words, fused by RRF. That needs `fastembed`,
`qdrant-client` and a ~2GB model download, so it is not what the tests run
against and not what an ordinary dev checkout needs. `LocalBackend` is the
default: the same interface over the name-based scoring the old store used, in
pure Python. Swapping one for the other changes no calling code.

    LocalBackend    default. No dependencies, no model, deterministic.
                    Matches names, not meanings — the old behaviour, kept honest
                    by `is_semantic = False` so callers can tell.
    QdrantBackend   production. Dense + sparse + RRF over our OWN entities.

INCREMENTALLY INDEXED, NOT BATCH-BUILT. The reference project indexes by an
offline GPU job over millions of SPARQL rows. Ours arrive a few at a time, as
users read, so every `add` upserts immediately and is searchable at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol

# ── the entity ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Entity:
    """A variable of an interaction. `iri` is a stable local id, not a KG IRI."""
    iri: str
    label: str
    gloss: str = ""

    def short(self) -> str:
        return self.label or self.iri

    def text(self) -> str:
        """What gets embedded: the name, and the gloss that disambiguates it."""
        return f"{self.label}. {self.gloss}".strip().rstrip(".")


@dataclass
class Match:
    entity: Entity
    score: float


# ── the interface ─────────────────────────────────────────────────────────


class VectorBackend(Protocol):
    """Where entity vectors live and how they are searched."""

    is_semantic: bool

    def add(self, entity: Entity) -> None:
        """Index one entity, searchable immediately."""

    def search(self, text: str, limit: int = 5) -> list[Match]:
        """The nearest entities to `text`, best first."""

    def all(self) -> list[Entity]:
        """Everything held."""


# ── the default backend ───────────────────────────────────────────────────


def slugify(text: str) -> str:
    """The id form of a name: lowercase, alphanumeric, single hyphens."""
    s = "".join(c if (c.isalnum() or c in " -_") else " " for c in text.lower())
    return "-".join(s.replace("_", " ").replace("-", " ").split())[:64] or "thing"


_NOISE = {"the", "a", "an", "of", "its", "their", "this", "that"}


def _singular(word: str) -> str:
    """A crude, deliberately timid de-pluraliser."""
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("ses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def match_key(text: str) -> str:
    """The key two names share when they name the same thing.

    Normalises away what carries no identity — case, punctuation, articles, a
    trailing plural — and nothing else.
    """
    words = [w for w in slugify(text).split("-") if w]
    kept = [w for w in words if w not in _NOISE] or words
    return "-".join(_singular(w) for w in kept)


def name_score(text: str, query: str) -> float:
    """How well `text` answers `query` — 0 for no match.

    Word-overlap with two adjustments that matter on short entity labels: a
    PREFIX hit outranks one in the middle, and an EXACT label wins outright.
    """
    words = [w for w in query.lower().split() if w]
    if not words:
        return 0.0
    hay = text.lower()
    parts = hay.replace("-", " ").split()

    def hit(w: str) -> bool:
        # A SHORT word must match at a word boundary: free substring matching is
        # right for long words ("infl" -> "inflammation") but on short labels it
        # is mostly noise ("as" would hit "case").
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
    elif any(p.startswith(words[0]) for p in hay.split()):
        score += 1.0
    return score


class LocalBackend:
    """Name-based matching, in pure Python. The default.

    `is_semantic` is False, and that is the point: this backend cannot see that
    "aspirin" and "acetylsalicylic acid" are one thing. Callers that care —
    a UI explaining why two entities were merged, a test asserting semantic
    behaviour — can ask rather than assume.
    """

    is_semantic = False

    def __init__(self) -> None:
        self._by_iri: dict[str, Entity] = {}

    def add(self, entity: Entity) -> None:
        self._by_iri[entity.iri] = entity

    def search(self, text: str, limit: int = 5) -> list[Match]:
        scored = []
        for e in self._by_iri.values():
            s = max(name_score(e.label, text), name_score(e.iri, text))
            if s > 0:
                scored.append(Match(entity=e, score=s))
        scored.sort(key=lambda m: (-m.score, m.entity.label))
        return scored[:limit]

    def all(self) -> list[Entity]:
        return list(self._by_iri.values())


class QdrantBackend:
    """Dense + sparse over our OWN entities, fused by RRF. Production.

    The mechanism is the one `sparql-llm` uses (`index_entities.py` +
    `retrieval_entities.py`), with three deliberate differences:

      * IT INDEXES OUR ENTITIES, not KG nodes harvested from SPARQL endpoints.
        Entities here are proposed freely and are not required to resolve.
      * IT UPSERTS INCREMENTALLY. The reference builds its index in an offline
        GPU batch; ours grows as users read.
      * IT PREFIXES for e5. `intfloat/multilingual-e5-large` is trained with
        "query: " / "passage: " markers and scores poorly without them; the
        reference omits this.

    Imports are deferred to __init__ so that merely importing this module costs
    nothing when the default backend is in use.
    """

    is_semantic = True

    def __init__(self, url: str = "http://localhost:6333",
                 collection: str = "entities",
                 dense_model: str = "intfloat/multilingual-e5-large",
                 sparse_model: str = "Qdrant/bm25") -> None:
        from fastembed import SparseTextEmbedding, TextEmbedding
        from qdrant_client import QdrantClient, models

        self._models = models
        self._client = QdrantClient(url=url)
        self._dense = TextEmbedding(dense_model)
        self._sparse = SparseTextEmbedding(sparse_model)
        self._collection = collection
        self._by_iri: dict[str, Entity] = {}

        if not self._client.collection_exists(collection):
            self._client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(
                    size=self._dense.embedding_size,
                    distance=models.Distance.COSINE,
                ),
                sparse_vectors_config={"sparse": models.SparseVectorParams()},
            )

    @staticmethod
    def _point_id(iri: str) -> str:
        """A stable uuid per iri, so re-adding an entity REPLACES its point."""
        import uuid
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"entity:{iri}"))

    def add(self, entity: Entity) -> None:
        models = self._models
        text = f"passage: {entity.text()}"
        dense = next(iter(self._dense.embed([text]))).tolist()
        sp = next(iter(self._sparse.embed([text])))
        self._client.upsert(
            collection_name=self._collection,
            points=[models.PointStruct(
                id=self._point_id(entity.iri),
                vector={"": dense,
                        "sparse": models.SparseVector(
                            indices=sp.indices.tolist(),
                            values=sp.values.tolist())},
                payload={"iri": entity.iri, "label": entity.label,
                         "gloss": entity.gloss},
            )],
        )
        self._by_iri[entity.iri] = entity

    def search(self, text: str, limit: int = 5) -> list[Match]:
        models = self._models
        q = f"query: {text}"
        dense = next(iter(self._dense.embed([q]))).tolist()
        sp = next(iter(self._sparse.embed([q])))
        res = self._client.query_points(
            collection_name=self._collection,
            prefetch=[
                models.Prefetch(using="", query=dense, limit=limit),
                models.Prefetch(
                    using="sparse",
                    query=models.SparseVector(indices=sp.indices.tolist(),
                                              values=sp.values.tolist()),
                    limit=limit),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
        ).points
        out = []
        for p in res:
            pay = p.payload or {}
            out.append(Match(
                entity=Entity(iri=pay.get("iri", ""), label=pay.get("label", ""),
                              gloss=pay.get("gloss", "")),
                score=float(p.score or 0.0)))
        return out

    def all(self) -> list[Entity]:
        return list(self._by_iri.values())


# ── the store ─────────────────────────────────────────────────────────────


class EntityIndex:
    """Entities, and the mapping of a NAME onto one of them.

    `resolve` is the whole point: given what a delegate just invented, hand back
    the entity we already hold if it is the same thing, and a new one otherwise.
    A false merge destroys a distinction the user drew; a missed merge only fails
    to share. WHEN IN DOUBT, DO NOT MERGE — hence `threshold`, and hence the
    exact-key check running first whatever the backend.
    """

    def __init__(self, backend: Optional[VectorBackend] = None,
                 threshold: float = 0.80) -> None:
        self.backend = backend if backend is not None else LocalBackend()
        self.threshold = threshold
        self._by_key: dict[str, Entity] = {}
        self._by_iri: dict[str, Entity] = {}

    @property
    def is_semantic(self) -> bool:
        return getattr(self.backend, "is_semantic", False)

    def add(self, entity: Entity) -> Entity:
        """Hold `entity`, and index it for later matching."""
        self._by_iri[entity.iri] = entity
        self._by_key.setdefault(match_key(entity.label), entity)
        self.backend.add(entity)
        return entity

    def get(self, iri: str) -> Optional[Entity]:
        return self._by_iri.get(iri)

    def resolve(self, label: str, gloss: str = "",
                iri: Optional[str] = None) -> Entity:
        """The entity this naming means — an existing one, or a new one.

        Order matters:
          1. the same iri is the same entity, outright;
          2. the same normalised NAME is the same entity — cheap, exact, and
             right whatever the backend;
          3. otherwise the nearest vector, if it clears `threshold`;
          4. otherwise a new entity.
        """
        if iri and iri in self._by_iri:
            return self._by_iri[iri]
        key = match_key(label)
        if key in self._by_key:
            return self._by_key[key]
        probe = f"{label}. {gloss}".strip().rstrip(".")
        for m in self.backend.search(probe, limit=1):
            if m.score >= self.threshold:
                return m.entity
        ent = Entity(iri=iri or self._mint(label), label=label, gloss=gloss)
        return self.add(ent)

    def _mint(self, label: str) -> str:
        base = slugify(label)
        if base not in self._by_iri:
            return base
        n = 2
        while f"{base}-{n}" in self._by_iri:
            n += 1
        return f"{base}-{n}"

    def search(self, text: str, limit: int = 5) -> list[Match]:
        return self.backend.search(text, limit=limit)

    def all(self) -> list[Entity]:
        return list(self._by_iri.values())

    def __len__(self) -> int:
        return len(self._by_iri)

    def load(self, entities: Iterable[Entity]) -> None:
        for e in entities:
            self.add(e)
