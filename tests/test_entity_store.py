"""The entity store — one node per entity, however it is named.

The delegate invents names, and asked twice about one thing it answers "COX
enzymes", then "the COX enzymes", then "COX enzyme". Each would otherwise become
a separate variable, so the sharing that makes a reused entity ONE node
(reading_desc §1) would be silently lost.

The matching is deliberately CONSERVATIVE: a false merge destroys a distinction
the user drew, a missed merge only fails to share. So the tests below weigh
"stays apart" at least as heavily as "merges".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from entity_store import EntityStore, match_key, slugify

FAILED = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILED.append(msg)


def test_slugify():
    print("\nslugify — the id form of a name")
    check(slugify("COX enzymes") == "cox-enzymes", "case and spaces")
    check(slugify("Cyclooxygenase (COX)") == "cyclooxygenase-cox",
          "punctuation does not leave doubled hyphens")
    check(slugify("  aspirin  ") == "aspirin", "surrounding space")
    check(slugify("COX_1") == "cox-1", "underscores read as separators")
    check(slugify("!!!") == "thing", "a name with nothing in it still yields an id")


def test_merges_variants():
    print("\nnames that mean the same thing resolve to ONE entity")
    s = EntityStore()
    base = s.resolve("cox-enzymes", "COX enzymes")
    for name in ["COX enzymes", "cox enzymes", "COX-enzymes", "the COX enzymes",
                 "COX enzyme", "COX ENZYMES", "the cox enzyme"]:
        check(s.resolve(slugify(name), name) is base, f"“{name}” is the same entity")
    check(len(s) == 1, "and the store holds just one")
    check(base.uses == 8, "every arrival is counted")
    check("the COX enzymes" in base.aliases, "the other names are kept as aliases")


def test_keeps_distinct_entities_apart():
    """The important half: near names that are NOT the same thing."""
    print("\nnames that differ in meaning stay APART")
    s = EntityStore()
    pairs = [
        ("COX-1", "COX-2"),                  # one character, different enzymes
        ("aspirin", "aspirin resistance"),   # a qualifier makes a new entity
        ("inflammation", "inflammatory"),    # related, not identical
        ("cell", "cell wall"),
        ("COX", "COX inhibitor"),
    ]
    for a, b in pairs:
        ea = s.resolve(slugify(a), a)
        eb = s.resolve(slugify(b), b)
        check(ea is not eb, f"“{a}” is not “{b}”")


def test_plural_rules_are_timid():
    print("\nthe de-pluraliser does not invent identities")
    # -s and -ies are safe; -ss must not be stripped, or class == clas.
    check(match_key("processes") == match_key("process"), "processes = process")
    check(match_key("bodies") == match_key("body"), "bodies = body")
    check(match_key("class") == match_key("classes"), "class = classes")
    check(match_key("gas") != match_key("ga"), "a short word is left alone")
    check(match_key("COX-1") == "cox-1", "an identifying digit survives")


def test_articles_dropped_but_not_the_whole_name():
    print("\narticles carry no identity — unless that is all there is")
    s = EntityStore()
    a = s.resolve("the-case", "the case")
    b = s.resolve("case", "case")
    check(a is b, "“the case” is “case”")
    # A name made only of noise words must still be an entity of its own.
    only_noise = s.resolve("the", "the")
    check(only_noise is not a, "a name that is ALL article is not merged away")


def test_resolve_normalises_the_namespace():
    print("\nthe delegate's `local:` prefix is not part of the name")
    s = EntityStore()
    a = s.resolve("local:aspirin", "Aspirin")
    b = s.resolve("aspirin", "Aspirin")
    check(a is b, "a namespaced id and a bare one are one entity")
    check(a.iri == "local:aspirin", "and the iri is namespaced exactly once")


def test_search_filters_like_the_kg_browser():
    """Type-to-filter, scored as `kg_store.search_nodes` did.

    Words are matched independently, so more words NARROW rather than exclude,
    and a prefix hit outranks one in the middle.
    """
    print("\nsearch — typing filters the entities")
    s = EntityStore()
    for lab in ["Aspirin", "Aspirin resistance", "COX enzymes", "COX-1",
                "COX-2", "reduced COX activity", "Inflammation"]:
        s.resolve(slugify(lab), lab)

    check([e.label for e in s.search("cox")][0] == "COX enzymes",
          "a prefix hit ranks above one in the middle")
    check("reduced COX activity" in [e.label for e in s.search("cox")],
          "but the middle hit is still offered")
    check([e.label for e in s.search("cox 1")][0] == "COX-1",
          "a second word promotes the entity carrying both")
    check([e.label for e in s.search("infl")] == ["Inflammation"],
          "a long prefix matches inside a word")
    check(s.search("zzz") == [], "no match yields nothing")
    check(len(s.search("")) == len(s), "an empty query lists everything")

    # An exact label must win outright, even against longer names containing it.
    check(s.search("aspirin")[0].label == "Aspirin",
          "an exact label wins over one that merely contains it")

    # Aliases are searchable: an entity found under one name stays reachable by
    # another it was proposed under.
    s.resolve("aspirin", "ASA")
    check(s.search("ASA")[0].label == "Aspirin", "aliases are searched too")


def test_short_words_match_at_a_word_boundary():
    """Free substring matching is noise on short labels — "as" would hit "case"."""
    print("\nshort query words match at a word boundary")
    s = EntityStore()
    for lab in ["aspirin", "aspirin as a whole", "the underlying case"]:
        s.resolve(slugify(lab), lab)

    found = [e.label for e in s.search("aspirin as")]
    check("the underlying case" not in found,
          "“as” does not drag in every entity containing “case”")
    check("aspirin as a whole" in found, "while the real matches remain")
    check([e.label for e in s.search("act")] == [],
          "a short word that starts no word matches nothing")


def test_merges_are_reported():
    print("\nmerges are reported, not done silently")
    s = EntityStore()
    s.resolve("cox-enzymes", "COX enzymes")
    s.take_merges()
    s.resolve("the-cox-enzymes", "the COX enzymes")
    merges = s.take_merges()
    check(merges == [("the COX enzymes", "COX enzymes")],
          "the merge is reported once, naming both sides")
    check(s.take_merges() == [], "and is not reported twice")


def test_store_is_authoritative_in_a_session():
    """The point of it all: one node in the term, not two."""
    print("\nin a session — a re-named entity is the SAME NODE")
    from reading_agent import Entity, Option, StepProposal
    from tests.test_reading_session import session, E

    s = session()
    r = s.open_reading(s.canon(Entity("local:cox-enzymes", "COX enzymes", "")))
    s.apply(r, StepProposal(kind="A", options=[]),
            s.canon_option(Option(kind="A", label="x",
                                  entity=Entity("local:aspirin", "Aspirin", ""))))
    # the delegate now names the seed differently
    s.apply(r, StepProposal(kind="A", options=[]),
            s.canon_option(Option(kind="A", label="y",
                                  entity=Entity("local:the-cox-enzymes",
                                                "the COX enzymes", ""))))
    seed_var = r.term.func.func
    reused = r.term.arg
    check(seed_var.iri == reused.iri, "the two namings share an iri")
    check(seed_var is reused,
          "and are ONE node of the sharing graph — the whole point")


def test_seeds_are_deduplicated():
    print("\ntwo seeds naming one thing do not start two readings")
    from reading_agent import Entity
    from tests.test_reading_session import session

    class Dup:
        total_cost_usd = 0.0

        def seed_entities(self, query, limit=6):
            return [Entity("local:cox", "COX enzymes", ""),
                    Entity("local:the-cox", "the COX enzyme", ""),
                    Entity("local:aspirin", "Aspirin", "")]

        def propose_step(self, **kw):
            from reading_agent import StepProposal
            return StepProposal(kind="none")

    s = session()
    s.agent = Dup()
    seeds = s.start()
    check(len(seeds) == 2, f"three proposed, two distinct (got {len(seeds)})")
    check([e.label for e in seeds] == ["COX enzymes", "Aspirin"],
          "the first naming is the one kept")


if __name__ == "__main__":
    for t in (test_slugify,
              test_merges_variants,
              test_keeps_distinct_entities_apart,
              test_plural_rules_are_timid,
              test_articles_dropped_but_not_the_whole_name,
              test_resolve_normalises_the_namespace,
              test_search_filters_like_the_kg_browser,
              test_short_words_match_at_a_word_boundary,
              test_merges_are_reported,
              test_store_is_authoritative_in_a_session,
              test_seeds_are_deduplicated):
        t()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for m in FAILED:
            print(f"  - {m}")
        sys.exit(1)
    print("all passed")
