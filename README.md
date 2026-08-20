# ChaBo-Deploy

Deployment topology for ChaBo — Dockerfiles, a `docker-compose` template, and a
GitHub composite action for pushing to Hugging Face Spaces. **No application source
lives here** (that's `ChaBo-Orchestrator`); this repo only owns *how a ChaBo instance
gets run somewhere*, consuming already-published orchestrator images.

## Deployment shapes

Four shapes are recognized org-wide (see `GUIDELINES.md` A5). Here's what actually exists
in this repo for each, stated plainly rather than implied:

| Shape | Status | Where |
|---|---|---|
| All-in-one container, demo-only | **Not built yet** | — (Qdrant/TEI/orchestrator as processes in one container is a different thing from `hf-spaces`, which is three independent Spaces) |
| Single-container + remote inference, PoC | Built | [`hf-spaces/`](hf-spaces/README.md) — three independent single-service Spaces (orchestrator/qdrant/chatui), each pointed at remote inference endpoints. Deploy `component: orchestrator` alone (pointed at a remote/managed Qdrant) for a minimal PoC, or pair with qdrant and/or chatui for a fuller deployment — not limited to PoC scale, a legitimate option for small/mid-scale production too |
| Compose stack, single VM | Built — **the reference for adopters** | [`compose/`](compose/README.md) |
| Compose stack split across VMs (GPU component isolated) | **Not built yet** | — |

## Corpus data

Where the vector data actually comes from differs by topology, but both converge on the
same point-payload shape:

```json
{
  "text": "chunk content here...",
  "metadata": { "...filterable and display fields, one level deep..." }
}
```

- **`hf-spaces`** — the qdrant Space self-loads on boot from a Hugging Face dataset
  (`EMBEDDING_DATASET`, columns `id`/`vector`/`payload`); the `payload` column is written
  into Qdrant verbatim. See [`hf-spaces/README.md`](hf-spaces/README.md#ingestion-mechanism).
- **`docker-compose-vm`** — `compose/qdrant-native.Dockerfile` has no self-loading mechanism; an external
  process is expected to push points directly to Qdrant's published API. See
  [`compose/README.md`](compose/README.md#payload-format-required-by-the-orchestrator-for-anyone-ingesting-data).

Only the *loading mechanism* differs — a payload written for one topology is structurally
valid for the other.

## Directory map

```
ChaBo-Deploy/
├── hf-spaces/                          # hf-spaces topology (see hf-spaces/README.md)
│   ├── orchestrator.Dockerfile
│   ├── qdrant/                         # self-loading Qdrant + Gradio API wrapper
│   ├── chatui/                         # ChaBo-ChatUI, optional third Space
│   └── boilerplate/                    # HF Space README/.gitattributes templates
├── compose/                            # docker-compose-vm topology (see compose/README.md)
│   ├── docker-compose.yml
│   ├── qdrant-native.Dockerfile        # plain Qdrant image, compose-only (no self-load)
│   └── ...
└── .github/actions/deploy-hf-space/    # composite action: renders + pushes to HF Spaces
```

Note for anyone still on the `v0.1.0-test` tag: it predates this layout — hf-spaces
content lived under top-level `templates/`, `vendored/qdrant/`, and `packaging/hf-spaces/`
instead of consolidated under `hf-spaces/`. Current `main` and every tag cut from here on
(including `hf-spaces-v0.1.0`) use the layout above.

## Versioning

GitHub Actions' `uses: repo@ref` always pins the *whole* repo at one ref, so the two
topologies version independently via separate tag namespaces:

- **`hf-spaces-vX.Y.Z`** — cuts affecting `hf-spaces/` or `.github/actions/deploy-hf-space/`.
- **`compose-vX.Y.Z`** — cuts affecting `compose/`.

A change touching only one topology only needs a tag in that topology's namespace. Pin to
the namespace matching what you're actually deploying.

## Docs

- [`hf-spaces/README.md`](hf-spaces/README.md) — hf-spaces topology mechanism
- [`compose/README.md`](compose/README.md) — docker-compose-vm topology mechanism
- `GUIDELINES.md` (org `.github` repo) — binding conventions this repo follows
