from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import rdflib
from rdflib.namespace import RDFS, OWL, SKOS, DCTERMS

from optimal_lambda import LamTerm, lam_to_dict, lam_from_dict

TTL_FILE = Path("/home/marek/uochb/ch/paperskg/bioinfo-lab.rdf")
LAMBDA_DB = Path(__file__).parent / "data" / "lambda_terms.json"
READINGS_DB = Path(__file__).parent / "data" / "readings.json"

EX = rdflib.Namespace("https://ahoj-db.org/kg#")
SCHEMA = rdflib.Namespace("https://schema.org/")
PROV_ACTIVITY = rdflib.URIRef("http://www.w3.org/ns/prov#Activity")


def load_graph() -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(str(TTL_FILE))
    return g


def load_lambda_db() -> list[dict]:
    if LAMBDA_DB.exists():
        return json.loads(LAMBDA_DB.read_text())
    return []


def save_lambda_db(records: list[dict]) -> None:
    LAMBDA_DB.parent.mkdir(parents=True, exist_ok=True)
    LAMBDA_DB.write_text(json.dumps(records, indent=2))


def load_readings_db() -> list[tuple[str, LamTerm]]:
    """Closed readings as (name, term).

    Terms may contain sharing fan-ins, so decoding goes through the
    sharing-aware loader when one is present (reading_state.lam_from_dict_shared);
    plain terms still load with the base decoder. Records written before the
    schema carried ontologies load unchanged — the ontology set is simply absent,
    which `load_readings_ontologies` reports as an empty list.
    """
    if not READINGS_DB.exists():
        return []
    data = json.loads(READINGS_DB.read_text())
    return [(r["name"], _decode_term(r["term"])) for r in data]


def load_readings_ontologies() -> dict[str, list[dict]]:
    """name -> the reading's stored ontology set, as raw dicts.

    Ontologies ARE stored when a reading is saved (reading_desc §1, O7): closing
    drops only the pointer set. They are survivors of refutation over that
    reading's whole construction, which enrichment cannot re-derive.

    Returned raw so this module stays free of an ontology_state import; the
    caller decodes with Ontology.from_dict.
    """
    if not READINGS_DB.exists():
        return {}
    data = json.loads(READINGS_DB.read_text())
    return {r["name"]: r.get("ontologies", []) for r in data}


def save_readings_db(readings: list[tuple[str, LamTerm]],
                     ontologies: dict[str, list[dict]] | None = None) -> None:
    """Persist closed readings, with their ontology sets when supplied (O7)."""
    READINGS_DB.parent.mkdir(parents=True, exist_ok=True)
    onts = ontologies or {}
    data = []
    for name, term in readings:
        rec = {"name": name, "term": _encode_term(term)}
        if onts.get(name):
            rec["ontologies"] = onts[name]
        data.append(rec)
    READINGS_DB.write_text(json.dumps(data, indent=2))


def _encode_term(term: LamTerm) -> dict:
    try:
        from reading_state import lam_to_dict_shared
        return lam_to_dict_shared(term)
    except ImportError:
        return lam_to_dict(term)


def _decode_term(d: dict) -> LamTerm:
    try:
        from reading_state import lam_from_dict_shared
        return lam_from_dict_shared(d)
    except ImportError:
        return lam_from_dict(d)


def local_name(iri: str) -> str:
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1]
    return iri


def node_label(g: rdflib.Graph, node: rdflib.URIRef) -> str:
    label = g.value(node, RDFS.label)
    if label:
        return str(label)
    iri = str(node)
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1].replace("_", " ")
    return iri


def node_description(g: rdflib.Graph, node: rdflib.URIRef) -> str:
    desc = g.value(node, DCTERMS.description)
    return str(desc) if desc else ""


def paper_reference(g: rdflib.Graph, paper) -> dict:
    """The bibliographic detail of a claim's source paper.

    A citation is a quotation, and a quotation without its source is not a
    citation — so the panel that shows the quoted sentence needs this alongside
    it. Everything here is already in the graph on the paper node: dcterms
    title/creator, schema datePublished/isPartOf, and the DOI as owl:sameAs.

    Returns {} when there is no paper, so callers can test it directly.
    """
    if not isinstance(paper, rdflib.URIRef):
        return {}
    authors = [str(a) for a in g.objects(paper, DCTERMS.creator)]
    doi = next((str(o) for o in g.objects(paper, rdflib.OWL.sameAs)
                if "doi.org" in str(o)), "")
    return {
        "iri": str(paper),
        "title": str(g.value(paper, DCTERMS.title) or ""),
        "authors": authors,          # absent on 38 of 54 papers in this graph
        "year": str(g.value(paper, SCHEMA.datePublished) or ""),
        "venue": str(g.value(paper, SCHEMA.isPartOf) or ""),
        "doi": doi,
        # how the paper entered the graph, and what it was read from: a caveat
        # recorded on the paper node is exactly what a reader weighing the claim
        # needs, and several carry one.
        "note": str(g.value(paper, DCTERMS.description) or ""),
    }


def node_types(g: rdflib.Graph, node: rdflib.URIRef) -> frozenset[str]:
    return frozenset(
        str(t).rsplit("/", 1)[-1].rsplit("#", 1)[-1]
        for t in g.objects(node, rdflib.RDF.type)
    )


def esc(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def search_nodes(g: rdflib.Graph, query: str, limit: int = 20) -> list[rdflib.URIRef]:
    query_words = query.lower().split()
    if not query_words:
        return []

    EX_NS  = "https://ahoj-db.org/kg#"
    PDB_NS = "https://www.rcsb.org/structure/"

    skip_types = (EX.Claim, PROV_ACTIVITY, OWL.ObjectProperty, OWL.DatatypeProperty)
    skip_nodes: set[rdflib.URIRef] = set()
    for typ in skip_types:
        for node in g.subjects(rdflib.RDF.type, typ):
            if isinstance(node, rdflib.URIRef):
                skip_nodes.add(node)

    candidates: set[rdflib.URIRef] = set()
    for s, _p, o in g:
        if isinstance(s, rdflib.URIRef):
            candidates.add(s)
        if isinstance(o, rdflib.URIRef):
            candidates.add(o)

    scored: list[tuple[float, rdflib.URIRef]] = []
    for node in candidates:
        if node in skip_nodes:
            continue
        iri = str(node)
        lbl = node_label(g, node).lower()
        loc = local_name(iri).replace("_", " ").lower()
        text = lbl + " " + loc
        hits = sum(1 for w in query_words if w in text)
        if hits == 0:
            continue
        boost = 2.0 if (iri.startswith(EX_NS) or iri.startswith(PDB_NS)) else 0.0
        scored.append((hits + boost, node))

    scored.sort(key=lambda x: -x[0])
    return [n for _score, n in scored[:limit]]


def _claim_predicate_label(g: rdflib.Graph, subject: rdflib.URIRef,
                           obj: rdflib.URIRef) -> str:
    """Find the rdfs:label of the direct predicate between subject and obj."""
    skip = {rdflib.RDF.type, RDFS.label, RDFS.comment,
            EX.subject, EX.object, EX.claimText, EX.citation,
            OWL.sameAs, SKOS.exactMatch}
    for pred in g.predicates(subject, obj):
        if pred in skip:
            continue
        lbl = g.value(pred, RDFS.label)
        if lbl:
            return str(lbl)
        return local_name(str(pred)).replace("_", " ")
    return "related to"


def get_claims_for_subject(g: rdflib.Graph, subject: rdflib.URIRef) -> list[dict]:
    claims = []
    for claim in g.subjects(EX.subject, subject):
        claim_text = str(g.value(claim, EX.claimText) or "")
        source_text = str(g.value(claim, EX.citation) or "")
        paper = paper_reference(g, g.value(claim, EX.derivedFromPaper))
        obj = g.value(claim, EX.object)
        obj_lbl = node_label(g, obj) if isinstance(obj, rdflib.URIRef) else str(obj or "?")
        obj_types = node_types(g, obj) if isinstance(obj, rdflib.URIRef) else frozenset()
        pred_label = (
            _claim_predicate_label(g, subject, obj)
            if isinstance(obj, rdflib.URIRef) else "?"
        )
        claims.append({
            "claim": claim,
            "claim_text": claim_text,
            "source_text": source_text,
            "paper": paper,
            "object": obj,
            "predicate_label": pred_label,
            "object_label": obj_lbl,
            "object_types": obj_types,
        })
    return sorted(claims, key=lambda c: c["predicate_label"])


def get_claims_for_object(g: rdflib.Graph, object_node: rdflib.URIRef) -> list[dict]:
    claims = []
    for claim in g.subjects(EX.object, object_node):
        claim_text = str(g.value(claim, EX.claimText) or "")
        source_text = str(g.value(claim, EX.citation) or "")
        paper = paper_reference(g, g.value(claim, EX.derivedFromPaper))
        subj = g.value(claim, EX.subject)
        subj_lbl = node_label(g, subj) if isinstance(subj, rdflib.URIRef) else str(subj or "?")
        subj_types = node_types(g, subj) if isinstance(subj, rdflib.URIRef) else frozenset()
        pred_label = (
            _claim_predicate_label(g, subj, object_node)
            if isinstance(subj, rdflib.URIRef) else "?"
        )
        claims.append({
            "claim": claim,
            "claim_text": claim_text,
            "source_text": source_text,
            "paper": paper,
            "subject": subj,
            "predicate_label": pred_label,
            "subject_label": subj_lbl,
            "subject_types": subj_types,
        })
    return sorted(claims, key=lambda c: c["predicate_label"])


def get_mappings_for_subject(g: rdflib.Graph, subject: rdflib.URIRef) -> list[dict]:
    mappings = []
    for pred_uri, pred_label in ((OWL.sameAs, "sameAs"), (SKOS.exactMatch, "exactMatch")):
        for obj in g.objects(subject, pred_uri):
            if not isinstance(obj, rdflib.URIRef):
                continue
            obj_lbl = node_label(g, obj)
            obj_types = node_types(g, obj)
            mappings.append({
                "mapping": obj,
                "object": obj,
                "predicate_label": pred_label,
                "object_label": obj_lbl,
                "object_types": obj_types,
                "is_exact": True,
            })
    return sorted(mappings, key=lambda m: m["predicate_label"])
