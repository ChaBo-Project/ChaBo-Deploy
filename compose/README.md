# docker-compose-vm topology

Deploys the ChaBo orchestrator (+ optionally Qdrant, TEI, an input-guard classifier, and
ChatUI) as co-located containers on a single VM via `docker compose`. This is the
alternative to the `hf-spaces` topology (`vendored/qdrant/`, `templates/orchestrator.Dockerfile`,
`.github/actions/deploy-hf-space/`), which deploys the orchestrator and a Gradio-wrapped
Qdrant as two separate Hugging Face Spaces.

Unlike the HF-Spaces topology, `chabo` here pulls the already-published
`ghcr.io/chabo-project/chabo-rag-orchestrator` image directly and mounts the instance's
`instance_config/` as a read-only volume — no per-instance image build required.

## Usage

1. Copy `.env.example` to `.env` in your instance repo, filling in `ORCHESTRATOR_TAG`,
   `INSTANCE_CONFIG_PATH`, `HF_TOKEN`, `QDRANT_API_KEY`, and `COMPOSE_PROFILES`.
2. If using the `chatui` profile, copy `chatui.env.local.template` to `chatui.env.local`
   and customize (model display name, prompt template, disclaimers).
3. Run:
   ```bash
   docker compose --env-file .env -f compose/docker-compose.yml up
   ```
   `COMPOSE_PROFILES` in `.env` controls which optional services start (see
   `.env.example` for the list). Leaving it empty starts only `chabo`, pointed at
   whatever remote Qdrant/embedding/reranker endpoints
   `instance_config/params.override.cfg` configures.

## Profiles

| Profile          | Service(s)     | When to use |
|------------------|----------------|-------------|
| `infra`          | `qdrant`       | Self-hosting Qdrant on this VM instead of a remote/managed instance |
| `local`          | `tei-embedding` | Self-hosting embedding instead of a remote HF endpoint |
| `local-reranker` | `tei-reranker` | Self-hosting reranking instead of a remote HF endpoint — only start this if `instance_config`'s `[retrieval] reranker_enabled = true`; the orchestrator skips reranking entirely when that flag is `false`, so there's no point running an idle reranker container in that case |
| `guard`          | `qwen3guard`   | `[input_guard] mode = classifier` |
| `chatui`         | `chatui`       | Deploying ChaBo-ChatUI alongside the orchestrator on this VM |

`local` and `local-reranker` are independent — reranking is a genuinely optional pipeline
stage in the orchestrator (`[retrieval] reranker_enabled`, default `true`; when `false` no
reranker endpoint is ever called, with a safe fallback to vector-search order even if a
live reranker call fails). Activate both (`COMPOSE_PROFILES=local,local-reranker`) for the
previous all-local behavior, or just `local` for local embedding with a remote/no reranker.

Scope note: this topology ships deployable artifacts only (Dockerfiles + a
docker-compose template) — there is no automated remote-deploy mechanism here yet
(unlike `deploy-hf-space`, which pushes to HF Spaces via a composite GitHub Action).
An instance repo runs `docker compose up` on its own VM however it already provisions
that VM.

## Network exposure

`tei-embedding`, `tei-reranker`, and `qwen3guard` have no `ports:` entry — deliberately.
`chabo` reaches all of them over the compose network's internal DNS (`http://tei-embedding:80`,
etc.), which works regardless of whether a port is published to the host. Publishing them
too would only make them reachable from outside the compose network (the VM's own network
interfaces, and from there potentially the public internet, depending on the VM's
firewall) — exposure with no corresponding benefit, since nothing outside the compose
network needs to reach them.

`qdrant` **is** published (6333/6334), unlike those three — on purpose, not an oversight.
Nothing in this repo ingests data into it (unlike the `hf-spaces` topology's
`vendored/qdrant/`, which self-loads from an HF dataset on boot); an external ingestion
process is expected to connect to Qdrant's API directly, so it has to be reachable from
outside the compose network. It's protected by the API keys described below, but that's
an application-layer check on top of an otherwise-open port, with no TLS in front of it —
**restrict who can actually reach 6333/6334 at the VM firewall level** (ideally to known
ingestion sources only, not the open internet), same as you would for any other
credential-protected service exposed on a public IP without TLS.

`chabo` (7860) and `chatui` (3000) also stay published — the actual query-time entry
points into this stack.

For the three still-unpublished services, this means you can no longer `curl
localhost:<port>` them directly from the VM's shell for debugging. Use `docker compose
exec chabo curl http://tei-embedding:80/...` (or any container already on the network)
instead. This isn't configurable via env var for those three — a fixed choice for this
file, not something we're maintaining an option to disable. If you specifically need one
of them published (e.g. for a debugging workflow that depends on it), fork the compose
file and add the `ports:` entry back yourself; that variant isn't something this repo
supports or tracks going forward.

## No Hugging Face access?

`chabo` still requires `HF_TOKEN` to be set even in a fully self-hosted, non-HF
deployment — the orchestrator checks it's non-empty at startup regardless of which
providers you've actually configured. If you have no HF account and nothing in
`instance_config` points at an HF-hosted endpoint, any placeholder string works.

## Qdrant API keys

Qdrant supports two key types (see [Qdrant's own docs](https://qdrant.tech/documentation/guides/security/#authentication)):
an **admin key** (`api_key`/`QDRANT__SERVICE__API_KEY`, full read/write) and a
**read-only key** (`read_only_api_key`/`QDRANT__SERVICE__READ_ONLY_API_KEY`, query-only).
`chabo`'s retriever only ever calls `query_points()` — it never writes to Qdrant, so it
never needs the admin key.

By default both are set to the same value (`QDRANT_API_KEY`) — nothing to configure,
same as before. To actually separate them (recommended once something else in your
pipeline does ingestion with the admin key), set `QDRANT_READ_ONLY_API_KEY` in `.env` to
a second, distinct secret; `chabo` picks it up automatically and the admin key stays
reserved for whatever writes to Qdrant.

## Payload format required by the orchestrator (for anyone ingesting data)

This topology doesn't ingest data itself (see "Network exposure" above) — whatever does
must write points matching the shape `chabo`'s retriever actually reads
(`retriever_orchestrator.py` in `ChaBo-Orchestrator`):

```json
{
  "text": "chunk content here...",
  "metadata": {
    "crop_type": ["maize", "wheat"],
    "title": "Some Document Title",
    "filename": "...", "project_id": "...", "document_source": "...", "page": 3
  }
}
```

- **`text`** — the chunk content. Falls back to a `page_content` key if `text` is absent
  (LangChain convention), but prefer `text`.
- **`metadata`** — a single nested object holding *everything* else: both filterable
  fields (`instance_config`'s `[metadata_filters] filterable_fields`, e.g. `crop_type`,
  `title` above) and display-only fields read by the generator
  (`[generator] CONTEXT_META_FIELDS`/`TITLE_META_FIELDS`).
- **Filterable fields must sit at the root of `metadata`, exactly one level deep** — the
  filter-building code only ever queries `metadata.<field>` (a single dot). Nesting a
  filterable field any deeper (e.g. `metadata.custom.crop_type`) means it will never
  match a query filter, silently.
