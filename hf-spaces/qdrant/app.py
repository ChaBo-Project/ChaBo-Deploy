import os
import time
import logging
import json
import gradio as gr
from qdrant_client import QdrantClient, models
from qdrant_client.http import models as rest
from typing import Optional, Dict, List, Any

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---
# Qdrant client connects to the local server started by start.sh
QDRANT_HOST = os.getenv("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
# Default collection name is set in start.sh/initialize_qdrant.py
DEFAULT_COLLECTION_NAME = os.getenv("COLLECTION_NAME")
# Prefers a dedicated read-only key if the Space sets one — this wrapper only ever
# calls query_points() (see below), never writes to Qdrant, so it doesn't need the
# admin key that initialize_qdrant.py uses for its one-time load. Falls back to the
# admin key if no read-only key is configured (same default-shared-key behavior as
# the compose topology's QDRANT_READ_ONLY_API_KEY — see compose/README.md's
# "Qdrant API keys").
QDRANT_API_KEY = os.getenv("QDRANT__SERVICE__READ_ONLY_API_KEY", os.getenv("QDRANT__SERVICE__API_KEY"))
DEFAULT_TOP_K = int(os.getenv("TOP_K", 10))
# Seconds per Qdrant request; unset keeps qdrant-client's own default.
# Raise it if filtered searches on large collections time out.
QDRANT_TIMEOUT = int(os.environ["QDRANT_TIMEOUT"]) if os.getenv("QDRANT_TIMEOUT") else None

def get_qdrant_client() -> QdrantClient:
    """Initialize the Qdrant client for the Gradio app."""
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, api_key=QDRANT_API_KEY, https=False, timeout=QDRANT_TIMEOUT)

client = get_qdrant_client()

def _build_qdrant_filter(filters: Dict[str, Any]) -> Optional[rest.Filter]:
    """
    Convert a plain {field: value} dict into a Qdrant rest.Filter.
    Payload fields are nested under 'metadata', so keys become 'metadata.<field>'.
    - list value   → MatchAny  (match any element in list)
    - scalar value → MatchValue (exact match)
    All conditions ANDed via must[]. Returns None if filters is empty or None.
    """
    if not filters:
        return None

    must_conditions = []
    for field, value in filters.items():
        qdrant_key = f"metadata.{field}"
        if isinstance(value, list):
            must_conditions.append(
                rest.FieldCondition(key=qdrant_key, match=rest.MatchAny(any=value))
            )
        else:
            must_conditions.append(
                rest.FieldCondition(key=qdrant_key, match=rest.MatchValue(value=value))
            )

    return rest.Filter(must=must_conditions)

# Dense vector name used when the caller sends none; hybrid collections name it "dense".
DEFAULT_DENSE_VECTOR_NAME = "dense"
DEFAULT_SPARSE_VECTOR_NAME = "sparse"
_dense_name_cache: Dict[str, Optional[str]] = {}


def _default_dense_vector_name(collection_name: str) -> Optional[str]:
    """
    The dense vector to search when the caller passes no vector_name.

    Unnamed-vector collections need none (None). Named-vector collections (hybrid) need
    one, or Qdrant answers 400 "Not existing vector name" — so callers that predate
    hybrid (which never send vector_name) keep working against a hybrid collection.
    """
    if collection_name in _dense_name_cache:
        return _dense_name_cache[collection_name]
    vectors = client.get_collection(collection_name).config.params.vectors
    name = None
    if isinstance(vectors, dict) and vectors:
        name = DEFAULT_DENSE_VECTOR_NAME if DEFAULT_DENSE_VECTOR_NAME in vectors else (
            next(iter(vectors)) if len(vectors) == 1 else None)
    _dense_name_cache[collection_name] = name
    return name


def _parse_sparse_vector(sparse_vector_json: str) -> rest.SparseVector:
    """Parse {"indices": [...], "values": [...]} into a Qdrant SparseVector."""
    data = json.loads(sparse_vector_json)
    if not isinstance(data, dict):
        raise ValueError("sparse vector must be a JSON object with 'indices' and 'values'.")
    indices, values = data.get("indices"), data.get("values")
    if not isinstance(indices, list) or not isinstance(values, list) or len(indices) != len(values):
        raise ValueError("sparse vector needs 'indices' and 'values' lists of equal length.")
    return rest.SparseVector(indices=[int(i) for i in indices], values=[float(v) for v in values])


def _jsonable_vector(vector: Any) -> Any:
    """Stored vectors as JSON: sparse vectors become {indices, values}."""
    if isinstance(vector, dict):
        return {name: _jsonable_vector(v) for name, v in vector.items()}
    if isinstance(vector, rest.SparseVector):
        return {"indices": vector.indices, "values": vector.values}
    return vector


# --- Core API Function ---
def query_points(
    query_vector_json: str,
    collection_name: str,
    top_k: int,
    query_filter: Optional[str] = None,
    vector_name: Optional[str] = None,
    with_vectors: bool = False,
    sparse_vector_json: Optional[str] = None,
    sparse_vector_name: Optional[str] = None,
    prefetch_top_k: Optional[float] = None,
    dense_weight: Optional[float] = None,
    sparse_weight: Optional[float] = None,
    rrf_k: Optional[float] = None,
) -> str:
    """
    Performs a vector search in Qdrant: dense-only, or hybrid (dense + sparse fused with
    weighted RRF) when sparse_vector_json is given.
    Args:
        query_vector_json (str): The query vector as a JSON string.
        collection_name (str): The name of the collection to search.
        top_k (int): The number of results to return.
        query_filter (str, optional): JSON string for the Qdrant filter object.
        vector_name (str, optional): Dense vector name ('using'). Default: none for an
            unnamed-vector collection, "dense" for a hybrid one.
        with_vectors (bool): Whether to return the stored vectors.
        sparse_vector_json (str, optional): {"indices": [...], "values": [...]}; turns on
            hybrid search. All arguments below apply only to hybrid search.
        sparse_vector_name (str, optional): Sparse vector name (default "sparse").
        prefetch_top_k (int, optional): Candidates per branch (default top_k, min top_k).
        dense_weight / sparse_weight (float, optional): RRF weights (default 0.5 / 0.5).
        rrf_k (int, optional): RRF smoothing constant (default 60).
    Returns:
        A JSON string of the search results or an error message.
    """

    # 1. Input Validation and Parsing (Mandatory fields)
    try:
        query_vector = json.loads(query_vector_json)
        if not isinstance(query_vector, list) or not all(isinstance(x, (int, float)) for x in query_vector):
            return json.dumps({
                "error": "Invalid Query Vector",
                "message": "Query vector must be a valid JSON array of numbers (e.g., [0.1, 0.2, 0.3])."
            })
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON", "message": "The query vector input is not a valid JSON string."})

    # 2. Parse Optional Parameters
    qdrant_filter = None

    if query_filter and query_filter.strip() not in ("", "{}"):
        try:
            filter_dict = json.loads(query_filter)
            qdrant_filter = _build_qdrant_filter(filter_dict)
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid Optional Parameters JSON", "message": "The 'query_filter' is not a valid JSON string."})
        except Exception as e:
            return json.dumps({"error": "Optional Parameters Error", "message": f"Could not process query filter: {e}"})

    sparse_vector = None
    if sparse_vector_json and sparse_vector_json.strip():
        try:
            sparse_vector = _parse_sparse_vector(sparse_vector_json)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return json.dumps({"error": "Invalid Sparse Vector", "message": str(e)})

    # 3. Qdrant Search Arguments
    top_k = int(top_k)
    search_args = {
        "collection_name": collection_name,
        "limit": top_k,
        "with_payload": True,
        "with_vectors": with_vectors,
    }

    # 4. Qdrant Search Execution
    try:
        dense_name = vector_name or _default_dense_vector_name(collection_name)

        if sparse_vector is None:
            # Dense-only: the original query shape
            search_args["query"] = query_vector
            if qdrant_filter:
                search_args["query_filter"] = qdrant_filter
            if dense_name:
                search_args["using"] = dense_name
        else:
            # Hybrid: both branches as prefetches (filter on each), fused by weighted RRF.
            # Same query as the orchestrator's native mode; needs Qdrant >= 1.17.
            branch_limit = max(int(prefetch_top_k or top_k), top_k)
            search_args["prefetch"] = [
                rest.Prefetch(query=query_vector, using=dense_name,
                              filter=qdrant_filter, limit=branch_limit),
                rest.Prefetch(query=sparse_vector,
                              using=sparse_vector_name or DEFAULT_SPARSE_VECTOR_NAME,
                              filter=qdrant_filter, limit=branch_limit),
            ]
            weights = [
                0.5 if dense_weight is None else float(dense_weight),
                0.5 if sparse_weight is None else float(sparse_weight),
            ]
            search_args["query"] = rest.RrfQuery(
                rrf=rest.Rrf(k=int(rrf_k or 60), weights=weights)
            )

        search_result = client.query_points(**search_args)

        results = []
        for hit in search_result.points:
            raw_content = hit.payload.get("text", "")

            result_dict = {
                "answer": raw_content,
                "answer_metadata": hit.payload.get("metadata", {}),
                "score": hit.score
            }
            if with_vectors and hit.vector:
                result_dict["vector"] = _jsonable_vector(hit.vector)

            results.append(result_dict)

        logger.info(f"Successfully retrieved {len(results)} documents from Qdrant in collection {collection_name}")
        return json.dumps(results)

    except Exception as e:
        logger.error(f"Qdrant search failed: {e}", exc_info=True)
        return json.dumps({"error": "Qdrant Error", "message": f"Search failed in {collection_name}. Detail: {str(e)}"})

def collection_info(collection_name: str) -> str:
    """
    Describe a collection so clients can adapt: dense/sparse vector names and the
    retrieval recipe that initialize_qdrant.py stores in the collection metadata.
    """
    try:
        info = client.get_collection(collection_name)
        params = info.config.params
        vectors = params.vectors
        return json.dumps({
            "collection_name": collection_name,
            "points_count": info.points_count,
            # null = one unnamed dense vector (the pre-hybrid layout)
            "dense_vectors": (
                {name: {"size": v.size, "distance": str(v.distance.value)} for name, v in vectors.items()}
                if isinstance(vectors, dict) else None
            ),
            "dense_vector_name": _default_dense_vector_name(collection_name),
            "sparse_vectors": {
                name: {"modifier": (v.modifier.value if v.modifier else None)}
                for name, v in (params.sparse_vectors or {}).items()
            },
            "metadata": getattr(info.config, "metadata", None) or {},
        })
    except Exception as e:
        logger.error(f"collection_info failed: {e}", exc_info=True)
        return json.dumps({"error": "Qdrant Error", "message": f"Cannot read collection {collection_name}. Detail: {str(e)}"})


# --- Gradio Interface Setup ---
# New inputs go at the end with defaults, so existing callers are unaffected.
iface = gr.Interface(
    fn=query_points,
    inputs=[
        gr.Textbox(label="1. Query Vector (JSON Array)", placeholder="[0.1, 0.2, 0.3, ...]", lines=3),
        gr.Textbox(label="2. Collection Name", value=DEFAULT_COLLECTION_NAME),
        gr.Number(label="3. Top K Results", value=DEFAULT_TOP_K, step=1),
        gr.Textbox(label="4. Query Filter (JSON, optional)", placeholder='{"key":"value", "key 2":"[value1, value2]"}', lines=2),
        gr.Textbox(label="5. Vector Name (optional)", placeholder="leave blank for default"),
        gr.Checkbox(label="6. Return Vectors", value=False),
        gr.Textbox(label="7. Sparse Vector (JSON, optional: turns on hybrid)", placeholder='{"indices": [1, 5], "values": [0.4, 0.9]}', lines=2),
        gr.Textbox(label="8. Sparse Vector Name (optional)", placeholder="sparse"),
        gr.Number(label="9. Prefetch Top K per branch (hybrid)", value=None, precision=0),
        gr.Number(label="10. Dense Weight (hybrid)", value=None),
        gr.Number(label="11. Sparse Weight (hybrid)", value=None),
        gr.Number(label="12. RRF k (hybrid)", value=None, precision=0),
    ],
    outputs=gr.Json(label="API Response"),
    title="Private Qdrant Retrieval API (Powered by Gradio)",
    api_name="query_points",
    description="Vector search endpoint. All optional parameters are now named arguments in both the UI and external API calls."
)

info_iface = gr.Interface(
    fn=collection_info,
    inputs=[gr.Textbox(label="Collection Name", value=DEFAULT_COLLECTION_NAME)],
    outputs=gr.Json(label="Collection Info"),
    title="Collection Info",
    api_name="collection_info",
    description="Vector names and retrieval recipe of a collection.",
)

app = gr.TabbedInterface([iface, info_iface], ["Query Points", "Collection Info"])

# Launch the Gradio application
if __name__ == "__main__":
    logger.info("Gradio application starting...")
    app.launch(server_name="0.0.0.0", server_port=7860)
