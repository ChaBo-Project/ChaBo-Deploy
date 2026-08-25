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
  `ghcr.io/chabo-project/hf-chat-ui` image, `custom_startup.sh` is the entrypoint (starts
  a local MongoDB, writes `DOTENV_LOCAL`'s content to `.env.local` if set, then launches
  ChatUI on port 3000). `hf-spaces/chatui/env.local.template` is a reference only (see
  "Required Space secrets/variables" below) — `render.sh` deliberately excludes it from
  what gets pushed to the Space.
- **`hf-spaces/boilerplate/`** — `README.md.template` (HF Space frontmatter) and
  `.gitattributes` (LFS rules for `*.snapshot`/`*.parquet`), stamped onto every render
  regardless of component.
- **`.github/actions/deploy-hf-space/`** — the composite action tying the above together:
  `render.sh` assembles one component's content into a temp dir, `push.sh` clones the
  target HF Space, wipes its non-`.git` content, copies the render in, and does a plain
  (non-force) push — a diverged Space (e.g. someone hand-edited it via the HF UI) fails
  loudly here instead of being silently overwritten.

  Action inputs: `component` (`orchestrator`/`qdrant`/`chatui`), `hf_space`
  (`org/space-name`), `image_tag` (published image tag — required for
  `component: orchestrator` (`chabo-rag-orchestrator` tag), optional for
  `component: chatui` (`hf-chat-ui` tag, defaults to the pinned version — override to
  pick up a new release without waiting on a `ChaBo-Deploy` release), unused for
  `component: qdrant`, whose version is this repo's own tag), `title` (Space README
  frontmatter title), `extra_content_path` (path in the
  *calling* repo to overlay, e.g. `orchestrator/instance_config` — only meaningful for
  `component: orchestrator`), and `hf_token` (HF token with write access to `hf_space`).

## Ingestion mechanism

`hf-spaces/qdrant/initialize_qdrant.py` runs once per container start, before the Gradio
server comes up:

1. Connects to the local Qdrant instance `start.sh` just launched (retries up to 10 times,
   5s apart, in case Qdrant isn't ready yet).
2. Checks whether `COLLECTION_NAME` already exists. If it does, skips straight to serving
   — **the collection is treated as already-loaded, not re-synced.**
3. If it doesn't exist: creates it (`EMBEDDING_DIMENSION`-sized vectors, cosine distance),
   then pulls `EMBEDDING_DATASET` from Hugging Face via `datasets.load_dataset` and
   `client.upsert()`s it into Qdrant in `BATCH_SIZE`-row batches.

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

## Required Space secrets/variables

Set by hand on each Space itself (Settings → Variables and secrets) — never by CI, and
not part of what `deploy-hf-space` pushes:

**orchestrator Space:** `HF_TOKEN`, `QDRANT_API_KEY`.

**qdrant Space:** `QDRANT__SERVICE__API_KEY` (admin key, required — used by
`initialize_qdrant.py`'s one-time load on boot, which needs write access),
`DATASET_READ_TOKEN` (HF token to read `EMBEDDING_DATASET`, only needed if that dataset
is private), `EMBEDDING_DATASET`, `COLLECTION_NAME`, `EMBEDDING_DIMENSION` — all required;
optionally `VECTOR_COLUMN_NAME` (default `vector`), `BATCH_SIZE` (default `200`), `TOP_K`
(default `10`, only affects the Gradio UI's default value, not a hard limit on API calls).

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
