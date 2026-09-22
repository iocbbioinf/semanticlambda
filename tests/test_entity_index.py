"""Tests for entity_index.py — entity mapping behind a swappable backend.

Run from the project root:  .venv/bin/python tests/test_entity_index.py

No model is downloaded and no Qdrant runs: the default LocalBackend is pure
Python, and a FakeSemantic backend stands in for the real one to prove the
interface is honoured.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entity_index import (
    Entity, EntityIndex, LocalBackend, Match, match_key, name_score,
)

FAILED: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  {name:<48} OK")
    else:
        print(f"  {name:<48} FAIL  got={got!r} want={want!r}")
        FAILED.append(name)


def ok(name, cond):
    check(name, bool(cond), True)


# ── name normalisation ────────────────────────────────────────────────────
print("=== match_key: namings that mean one thing ===")

check("case", match_key("COX enzymes"), match_key("cox enzymes"))
check("article", match_key("the COX enzymes"), match_key("COX enzymes"))
check("plural", match_key("COX enzyme"), match_key("COX enzymes"))
check("punctuation", match_key("COX-enzymes!"), match_key("COX enzymes"))
ok("genuinely different names stay apart",
   match_key("COX-1") != match_key("COX-2"))

# ── the default backend ───────────────────────────────────────────────────
print("=== LocalBackend: names, and honest about it ===")

idx = EntityIndex()
ok("default is not semantic", not idx.is_semantic)

asp = idx.add(Entity("aspirin", "aspirin", "a painkiller"))
cox = idx.add(Entity("cox", "COX enzymes", "cyclooxygenase"))
check("holds two", len(idx), 2)

check("same iri resolves to the same entity",
      idx.resolve("anything", iri="aspirin").iri, "aspirin")
check("same name resolves", idx.resolve("the COX enzyme").iri, "cox")
check("different name mints a new one",
      idx.resolve("inflammation").iri, "inflammation")
check("now holds three", len(idx), 3)

# minting does not collide
idx2 = EntityIndex()
a1 = idx2.add(Entity("aspirin", "aspirin"))
a2 = idx2.resolve("Aspirin!", iri=None)
check("normalised duplicate merges", a2.iri, "aspirin")

# search ranks exact and prefix above middle hits
idx3 = EntityIndex()
idx3.add(Entity("cox", "COX enzymes"))
idx3.add(Entity("reduced", "reduced COX activity"))
hits = [m.entity.iri for m in idx3.search("cox")]
check("prefix outranks middle", hits[0], "cox")

# ── the interface ─────────────────────────────────────────────────────────
print("=== the backend is swappable ===")


class FakeSemantic:
    """A stand-in for Qdrant: a hand-written synonym table.

    Proves EntityIndex drives any VectorBackend, without a model download.
    """

    is_semantic = True
    SYNONYMS = {
        "acetylsalicylic acid": "aspirin",
        "asa": "aspirin",
        "cyclooxygenase": "cox",
    }

    def __init__(self):
        self._by_iri = {}

    def add(self, entity):
        self._by_iri[entity.iri] = entity

    def search(self, text, limit=5):
        probe = text.lower().split(".")[0].strip()
        target = self.SYNONYMS.get(probe)
        if target and target in self._by_iri:
            return [Match(entity=self._by_iri[target], score=0.95)]
        return []

    def all(self):
        return list(self._by_iri.values())


idx4 = EntityIndex(backend=FakeSemantic())
ok("reports itself semantic", idx4.is_semantic)
idx4.add(Entity("aspirin", "aspirin", "a painkiller"))

# the thing the name-based store cannot do
check("merges by MEANING, not spelling",
      idx4.resolve("acetylsalicylic acid").iri, "aspirin")
check("and another naming", idx4.resolve("ASA").iri, "aspirin")
check("still one entity", len(idx4), 1)

# the same probe against the default backend does NOT merge — the honest
# difference between the two backends
idx5 = EntityIndex()
idx5.add(Entity("aspirin", "aspirin", "a painkiller"))
check("LocalBackend cannot see the synonym",
      idx5.resolve("acetylsalicylic acid").iri, "acetylsalicylic-acid")

print("=== when in doubt, do not merge ===")

idx6 = EntityIndex(backend=FakeSemantic(), threshold=0.99)
idx6.add(Entity("aspirin", "aspirin"))
check("a near miss below threshold stays apart",
      idx6.resolve("acetylsalicylic acid").iri, "acetylsalicylic-acid")

# exact-key matching runs FIRST, whatever the backend says
idx7 = EntityIndex(backend=FakeSemantic())
idx7.add(Entity("cox", "COX enzymes"))
check("exact name beats the vector", idx7.resolve("the COX enzyme").iri, "cox")

# ── incremental ───────────────────────────────────────────────────────────
print("=== indexed incrementally, searchable at once ===")

idx8 = EntityIndex()
idx8.add(Entity("new", "brand new thing"))
check("searchable immediately",
      [m.entity.iri for m in idx8.search("brand new thing")], ["new"])

idx9 = EntityIndex(backend=FakeSemantic())
e = idx9.resolve("aspirin")            # minted, not pre-added
check("a resolved entity is indexed too",
      idx9.resolve("acetylsalicylic acid").iri, e.iri)

print()
if FAILED:
    print(f"FAILED ({len(FAILED)}): " + ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
