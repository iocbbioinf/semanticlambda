"""Run the web app.

    .venv/bin/python serve.py                  mock delegate, in-memory store
    .venv/bin/python serve.py --claude         a real delegate
    .venv/bin/python serve.py --mongo --qdrant production backends

Everything is chosen HERE and injected, so nothing downstream imports a
backend it does not use: no Mongo driver is loaded unless --mongo is given, and
no embedding model unless --qdrant is.
"""

from __future__ import annotations

import argparse
import os

from answer_agent import EchoAnswerer, TransportAnswerer
from entity_index import EntityIndex
from interaction_store import InteractionStore, JSONBackend, MemoryBackend
from web_app import create_app


def build(args):
    # ── the delegate ──────────────────────────────────────────────────────
    if args.claude or args.openai or args.einfra:
        from reading_transport import ClaudeCLITransport, OpenAITransport
        if args.claude:
            # `model=None` would override the class default, not fall back to it
            tr = ClaudeCLITransport(
                model=args.model or ClaudeCLITransport.DEFAULT_MODEL)
        else:
            base = args.base_url
            if args.einfra:
                base = base or OpenAITransport.EINFRA_BASE_URL
            json_mode = (True if args.json_mode
                         else (False if args.strict_schema else None))
            # `gpt-4o` does not exist on someone else's endpoint, so a
            # --base-url without a --model is a mistake worth naming.
            model = args.model
            if model is None:
                if base:
                    raise SystemExit(
                        "--model is required with --base-url/--einfra: the "
                        "default (gpt-4o) is an OpenAI model. List what the "
                        "endpoint offers with\n"
                        f"  curl -H \"Authorization: Bearer $E_INFRA_API_TOKEN\" "
                        f"{base.rstrip('/')}/models")
                model = OpenAITransport.DEFAULT_MODEL
            tr = OpenAITransport(model=model, base_url=base,
                                 json_mode=json_mode)
        answerer = TransportAnswerer(tr)
        from mock_source import AgentSource
        source = AgentSource(tr)
    else:
        from mock_source import MockSource
        source = MockSource()
        answerer = EchoAnswerer()

    # ── the store ─────────────────────────────────────────────────────────
    if args.mongo:
        from interaction_store import MongoBackend
        backend = MongoBackend(os.environ.get("MONGO_URL",
                                              "mongodb://localhost:27017"))
    elif args.json:
        backend = JSONBackend(args.json)
    else:
        backend = MemoryBackend()

    # ── entities ──────────────────────────────────────────────────────────
    if args.qdrant:
        from entity_index import QdrantBackend
        entities = EntityIndex(QdrantBackend(
            os.environ.get("QDRANT_URL", "http://localhost:6333")))
    else:
        entities = EntityIndex()

    return create_app(source=source, store=InteractionStore(backend),
                      answerer=answerer, entities=entities,
                      verbose=args.verbose)


def main():
    p = argparse.ArgumentParser(description="semantic lambda web app")
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    p.add_argument("--claude", action="store_true", help="delegate to `claude -p`")
    p.add_argument("--openai", action="store_true",
                   help="delegate to an OpenAI-compatible API")
    p.add_argument("--einfra", action="store_true",
                   help="delegate to the e-INFRA/CERIT endpoint "
                        "(https://llm.ai.e-infra.cz/v1/); needs "
                        "E_INFRA_API_TOKEN")
    p.add_argument("--base-url", default=None, metavar="URL",
                   help="an OpenAI-compatible endpoint other than OpenAI's")
    p.add_argument("--json-mode", action="store_true",
                   help="ask for a JSON object and put the schema in the "
                        "prompt (for providers without strict json_schema). "
                        "The default for any --base-url")
    p.add_argument("--strict-schema", action="store_true",
                   help="force structured outputs even with --base-url")
    p.add_argument("--model", default=None)
    p.add_argument("--mongo", action="store_true", help="store in MongoDB")
    p.add_argument("--json", default=None, metavar="PATH", help="store in a JSON file")
    p.add_argument("--qdrant", action="store_true", help="entities in Qdrant")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="show the calculus: subqueries, the unclear point and "
                        "what it maps to, which case, the term as it grows, "
                        "and the interaction question that gets saved")
    args = p.parse_args()

    import uvicorn
    # ONE WORKER, by decision: sessions are in memory, so a second worker would
    # not see the first one's interactions.
    uvicorn.run(build(args), host=args.host, port=args.port, workers=1)


app = None  # set when run under a server that imports `serve:app`

if __name__ == "__main__":
    main()
