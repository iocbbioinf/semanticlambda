# Deploying

## Local

```bash
docker compose up --build
# http://localhost:8000
```

Or without Docker, on the defaults (mock delegate, in-memory store, name-based
entity matching — no model, no database, no cost):

```bash
.venv/bin/python serve.py
```

`serve.py` chooses every backend and injects it, so nothing is imported that is
not selected:

| flag | effect |
|---|---|
| *(none)* | mock delegate, in-memory store, name matching |
| `--verbose` / `-v` | show the calculus: subqueries, the unclear point and what it maps to, which case, the term as it grows, the λ-question saved on resume |
| `--claude` | delegate to `claude -p` (default model `sonnet`) |
| `--openai` | delegate to an OpenAI-compatible API (default `gpt-4o`) |
| `--einfra` | delegate to e-INFRA/CERIT, `https://llm.ai.e-infra.cz/v1/` |
| `--base-url URL` | any other OpenAI-compatible endpoint |
| `--model NAME` | required with `--einfra`/`--base-url` |
| `--json-mode` / `--strict-schema` | force the reply format either way |
| `--mongo` | store in MongoDB (`MONGO_URL`) |
| `--json PATH` | store in a JSON file |
| `--qdrant` | entity vectors in Qdrant (`QDRANT_URL`) — downloads a ~2GB model on first run |

### e-INFRA / CERIT

Generate a token in Open WebUI under *Account → API keys* (it starts with
`sk-`), then:

```bash
export E_INFRA_API_TOKEN=sk-...
# see what the endpoint offers
curl -H "Authorization: Bearer $E_INFRA_API_TOKEN" https://llm.ai.e-infra.cz/v1/models
.venv/bin/python serve.py --einfra --model llama3.3:latest -v
```

**Why `--model` is required there.** The default is `gpt-4o`, which is an
OpenAI model and does not exist on anyone else's endpoint; guessing silently
would fail on the first delegation instead of at startup.

**Why a third-party endpoint uses JSON mode.** The app asks the delegate for a
schema-shaped reply. Strict `response_format: json_schema` is an OpenAI
extension, and e-INFRA's docs say plainly that "not all endpoints are
supported"; their models are Ollama-backed (llama3.3, deepseek-r1, qwen2.5).
So any `--base-url` defaults to `json_object` with the schema written into the
prompt, and the reply is parsed tolerantly — a ```` ```json ```` fence, a
`<think>` block or a sentence of preamble are all recovered rather than failing
the step. Override with `--strict-schema` if a provider does support it.

## Production (Rancher, rancher.cloud.e-infra.cz)

```bash
# 1. build and push
docker build -t cerit.io/YOUR-NAMESPACE/semanticlambda:0.1.0 .
docker push  cerit.io/YOUR-NAMESPACE/semanticlambda:0.1.0

# 2. secrets (all optional — the app runs without them)
kubectl create secret generic semanticlambda-secrets \
  --from-literal=turnstile-secret=... \
  --from-literal=turnstile-site-key=... \
  --from-literal=openai-api-key=...

# 3. stores first, then the app
kubectl apply -f deploy/k8s/data.yaml
kubectl apply -f deploy/k8s/app.yaml
```

Before applying, edit in `app.yaml`: the `image:` path, and the two
`CHANGEME.dyn.cloud.e-infra.cz` hostnames.

## Things that are decisions, not defaults

**`replicas: 1` is load-bearing.** Sessions live in the pod's memory
(`web_sessions.py`), so a second replica would serve requests that cannot see
the first one's interactions — a user would be bounced to the query form
mid-reading. `strategy: Recreate` follows from the same fact, and the ingress
sets a sticky-session cookie so that raising the replica count without reading
this does not immediately break correctness. Making the app scalable is not a
manifest change; it is backing `SessionRegistry` with Mongo, which is why that
interface exists.

**A restart loses interactions in flight.** Saved queries are safe in Mongo;
half-finished readings are not. That is the accepted trade.

**Turnstile is off unless configured, and fails closed when it is.** An
unconfigured Turnstile is a deployment without it (so a dev checkout runs). A
configured one that cannot be reached REFUSES the submission, because the
alternative turns a Cloudflare outage into an open endpoint. The per-IP rate
limit is always on and runs first, so a flood costs no network call.

**Qdrant is rebuildable.** Every entity is also in Mongo, so losing that volume
costs a reindex, not data. Mongo's volume is the one that matters.

## Verified

The image was built and run: it serves on **uid 10001, non-root**, with a
**read-only root filesystem** plus a `/tmp` mount — the `securityContext` in
`app.yaml` is tested, not aspirational. A full interaction (name → query →
step → resume → answer) was walked through the running container.

Not yet verified: the manifests have not been applied to a live cluster.
