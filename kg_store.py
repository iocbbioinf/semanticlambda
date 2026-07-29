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
    if READINGS_DB.exists():
        data = json.loads(READINGS_DB.read_text())
        return [(r["name"], lam_from_dict(r["term"])) for r in data]
    return []


def save_readings_db(readings: list[tuple[str, LamTerm]]) -> None:
    READINGS_DB.parent.mkdir(parents=True, exist_ok=True)
    data = [{"name": name, "term": lam_to_dict(term)} for name, term in readings]
    READINGS_DB.write_text(json.dumps(data, indent=2))


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
