# hf-spaces topology

Deploys the ChaBo orchestrator, a Gradio-wrapped Qdrant, and (optionally) ChaBo-ChatUI as
up to three separate Hugging Face Spaces, each pushed by the `deploy-hf-space` composite
action (`.github/actions/deploy-hf-space/`). This is the alternative to the
`docker-compose-vm` topology (`compose/`), which co-locates everything as containers on a
single VM instead.

The qdrant Space self-loads its own data from a Hugging Face dataset on boot, rather than
pulling from an already-running remote Qdrant (see "Ingestion mechanism" below). All
three components are independent deploys: the action takes
`component: orchestrator`, `component: qdrant`, or `component: chatui` as separate
invocations, so a remote-inference-only PoC that only deploys the orchestrator (pointed
at some other, already-running Qdrant) is possible without touching the qdrant or chatui
Spaces at all.

## Layout

These pieces have no shared parent folder beyond the repo root — each is a distinct
concern, not a self-contained deployable unit on its own:

- **`hf-spaces/orchestrator.Dockerfile`** — `FROM` the already-published
  `ghcr.io/chabo-project/chabo-rag-orchestrator:{{TAG}}` image, `COPY`s in the calling
  instance repo's `instance_config/`. No application source lives here (see Guidelines
  A3.4) — this only bakes instance config into a buildable Space image, since HF Spaces
  need a Dockerfile at build time.
- **`hf-spaces/qdrant/`** — a self-loading Qdrant wrapper: `Dockerfile` builds on the
  official `qdrant/qdrant` image, `start.sh` is the entrypoint (starts Qdrant locally,
  runs `initialize_qdrant.py`, then launches `app.py`'s Gradio server in the foreground),
  `app.py` exposes a `query_points` Gradio API in front of the local Qdrant instance.
- **`hf-spaces/chatui/`** — `Dockerfile` builds on the published
  `ghcr.io/chabo-project/chabo-chatui-db` image, `custom_startup.sh` is the entrypoint (starts
  a local MongoDB, writes `DOTENV_LOCAL`'s content to `.env.local` if set, then launches
  ChatUI on port 3000). `hf-spaces/chatui/env.local.template` is a reference only (see
  "Required Space secrets/variables" below) — `render.sh` deliberately excludes it from
  what gets pushed to the Space. `hf-spaces/chatui/PRIVACY.md` is baked into the image
  and *is* pushed — edit it (or overlay your own via `extra_content_path`) before deploying
  if the default AI Act transparency placeholder needs real content.
- **`hf-spaces/boilerplate/`** — `README.md.template` (HF Space frontmatter) and
  `.gitattributes` (LFS rules for `*.snapshot`/`*.parquet`), stamped onto every render
  regardless of component.
- **`.github/actions/deploy-hf-space/`** — the composite action tying the above together:
  `render.sh` assembles one component's content into a temp dir, `push.sh` clones the
  target HF Space, wipes its non-`.git` content, copies the render in, and does a plain
  (non-force) push — a diverged Space (e.g. someone hand-edited it via the HF UI) fails
  loudly here instead of being silently overwritten.

  Action inputs: `component` (`orchestrator`/`qdrant`/`chatui`), `hf_space`
  (`org/space-name`), `image_tag` (published image tag — required for both
  `component: orchestrator` (`chabo-rag-orchestrator` tag) and
  `component: chatui` (`chabo-chatui-db` tag; no silent default, since a stale pin
  would deploy silently), unused for `component: qdrant`, whose version is this
  repo's own tag), `title` (Space README
  frontmatter title), `extra_content_path` (folder path relative to the
  *calling* repository checkout: for `component: orchestrator`, its contents become
  `instance_config/`; for `component: chatui`, the folder must contain `PRIVACY.md`,
  which replaces the default privacy file, and may contain `static/<name>/` — see
  "Custom ChatUI logo" below), and `hf_token` (HF token with write access to `hf_space`).

## Custom ChatUI logo

ChatUI loads its logo and icons from `/app/build/client/$PUBLIC_APP_ASSETS/`. The image
ships `chabo-official`, `chatui` and `huggingchat`. For your own set, put a
`static/<name>/` folder in the chatui `extra_content_path` folder and set
`PUBLIC_APP_ASSETS=<name>` in `DOTENV_LOCAL`. `render.sh` pushes it as `static/`, and the
Space Dockerfile copies it next to the built-in sets (same name = override). Files read:
`logo.svg`, `favicon.ico`, `icon.svg`, `apple-touch-icon.png`, `icon-512x512.png` — copy
ChaBo-ChatUI's `static/chabo-official/` as a starting point. `*.png`/`*.ico` go through
LFS (boilerplate `.gitattributes`). An unknown name doesn't fail: the page loads without
a logo, so check that `/<name>/logo.svg` returns `content-type: image/svg+xml`.

## Ingestion mechanism

`hf-spaces/qdrant/initialize_qdrant.py` runs once per container start, before the Gradio
server comes up:

1. Connects to the local Qdrant instance `start.sh` just launched (retries up to 10 times,
   5s apart, in case Qdrant isn't ready yet).
2. Checks whether `COLLECTION_NAME` already exists. If it does, skips straight to serving
   — **the collection is treated as already-loaded, not re-synced.**
3. If it doesn't exist: creates it (`EMBEDDING_DIMENSION`-sized vectors, cosine distance),
   creates a payload index on `metadata.<field>` for each `FILTERABLE_FIELDS` entry
   (`str`/`list` → keyword, `int` → integer), then pulls `EMBEDDING_DATASET` from Hugging
   Face via `datasets.load_dataset` and `client.upsert()`s it into Qdrant in
   `BATCH_SIZE`-row batches. With `SPARSE_MODEL` set, the collection is built for hybrid
   retrieval instead (see below).

The dataset is expected to have `id`, a vector column (default name `vector`, overridable
via `VECTOR_COLUMN_NAME`), and `payload` columns; `payload` is parsed with
`ast.literal_eval` if it arrives as a string, then written into Qdrant verbatim as the
point's payload — no transformation. This means **`EMBEDDING_DATASET` is the durable
source of truth, not the Space's own disk**: a Space restart or redeploy that wipes local
storage just re-pulls from the same dataset on next boot, as long as the collection name
doesn't change.

The payload shape (`{"text": ..., "metadata": {...}}`) matches what the orchestrator's
retriever expects — `hf-spaces/qdrant/app.py`'s `query_points` reads
`hit.payload.get("text", "")` and `hit.payload.get("metadata", {})` directly, confirming
this on the read side.

### Hybrid retrieval (optional)

Set `SPARSE_MODEL` (e.g. `Qdrant/bm25`) and optionally `SPARSE_LANGUAGE` (default
`english`; fastembed's BM25 supports 18 languages) to build a dense + BM25 collection:

- a named `dense` vector (from the dataset, as before) and a `sparse` vector with
  `modifier=IDF`, BM25-encoded at boot from each payload's `text` — the dataset needs no
  new columns;
- the recipe (vector names, model, language, fastembed version, text field) stored in
  the collection metadata under `chabo_retrieval`. The orchestrator reads it at startup,
  so its query encoding always matches the documents.

Unset, the collection is built exactly as before (one unnamed dense vector). The flag only
applies when the collection is created: an existing dense collection is never changed
(a warning says so). Storage is ephemeral, so a restart rebuilds it; on persistent storage
use a new `COLLECTION_NAME`. An invalid model or language is logged and no collection is
created. The `Qdrant/bm25` assets (stopword lists for all 18 languages, ~128 KB) are baked
into the image.

Only `Qdrant/bm25` is tested. Other fastembed sparse models are passed through but not
supported: the sparse vector is always created with `modifier=IDF`, which suits BM25 but
not neural sparse models such as SPLADE (their weights already carry term importance), and
those models (hundreds of MB) would download on every boot.

Turning on hybrid in the orchestrator also needs `[retrieval] hybrid_enabled = true`
(orchestrator 0.9.3 or later for `[qdrant] mode = gradio`). Orchestrators that predate
hybrid keep working against a hybrid collection, dense-only: `query_points` searches the
`dense` vector when no `vector_name` is sent.

### Gradio API

- **`query_points`** — vector search. Dense-only by default; passing `sparse_vector_json`
  (`{"indices": [...], "values": [...]}`) runs hybrid search: dense and sparse prefetches
  (`prefetch_top_k` each, metadata filter on both) fused by weighted RRF (`dense_weight`,
  `sparse_weight`, `rrf_k`). All hybrid arguments are optional and trail the original ones.
- **`collection_info`** — the collection's dense/sparse vector names, point count and
  metadata (the hybrid recipe). Read once at startup by orchestrators running hybrid in
  gradio mode.

## Required Space secrets/variables

Not part of what `deploy-hf-space` pushes. `instance-example`'s deploy workflow sets
them; otherwise set them by hand on each Space (Settings → Variables and secrets):

**orchestrator Space:** `HF_TOKEN`, `QDRANT_API_KEY`.

**qdrant Space:** `QDRANT__SERVICE__API_KEY` (admin key, required — used by
`initialize_qdrant.py`'s one-time load on boot, which needs write access),
`DATASET_READ_TOKEN` (HF token to read `EMBEDDING_DATASET`, only needed if that dataset
is private), `EMBEDDING_DATASET`, `COLLECTION_NAME`, `EMBEDDING_DIMENSION` — all required;
optionally `VECTOR_COLUMN_NAME` (default `vector`), `BATCH_SIZE` (default `200`), `TOP_K`
(default `10`, only affects the Gradio UI's default value, not a hard limit on API calls),
`FILTERABLE_FIELDS` (same `field:type,...` value as the orchestrator's
`[metadata_filters] filterable_fields`; indexes those fields so filtered search stays fast
on large collections; the declared type must match the stored values — `int` only
matches integers, `str`/`list` only strings), `QDRANT_TIMEOUT` (seconds per search
request; unset keeps qdrant-client's default), `SPARSE_MODEL` / `SPARSE_LANGUAGE` (hybrid
retrieval, see "Hybrid retrieval" above; unset = dense-only).

Also optional: `QDRANT__SERVICE__READ_ONLY_API_KEY` — a read-only key for `query_points`,
separate from the admin key `initialize_qdrant.py` uses for its write. Worth setting since
this Gradio API's actual reachability depends entirely on the Space's own visibility
setting (public/private) — least-privilege here is a real hedge against that setting being
public, by accident or by design (see "demo-only"/"PoC" in the root README's deployment
shapes).

**chatui Space:** `DOTENV_LOCAL` — the full contents of an `.env.local` file, set as one
multi-line Space secret (`custom_startup.sh` writes it verbatim to `/app/.env.local` on
boot, which ChatUI's own config loader then reads as an override layer). Start from
`hf-spaces/chatui/env.local.template`, but **the `endpoints[].url`/`streamingFileUploadUrl`
must point at the orchestrator Space's public HTTPS URL**
(`https://<user-or-org>-<space-name>.hf.space/chatfed-ui-stream`), not
`http://chabo:7860/...` — that hostname only resolves inside a compose network, which
these two Spaces don't share. Getting this wrong doesn't fail the deploy; chatui just
can't reach the orchestrator at query time.

The step-by-step walkthrough for setting these on a real Space lives in the consuming
instance repo (e.g. `instance-example`'s `qdrant/README.md` and
`orchestrator/README.md`), per this org's established split: mechanism here, walkthrough
there. This doc explains what the pieces are and why, not a click-by-click guide.
