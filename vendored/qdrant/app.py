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
# The Qdrant API key is optional here if not set, as it's a local connection
QDRANT_API_KEY = os.getenv("QDRANT__SERVICE__API_KEY")
DEFAULT_TOP_K = int(os.getenv("TOP_K", 10))

def get_qdrant_client() -> QdrantClient:
    """Initialize the Qdrant client for the Gradio app."""
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, api_key=QDRANT_API_KEY, https=False)

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

# --- Core API Function ---
def query_points(
    query_vector_json: str,
    collection_name: str,
    top_k: int,
    query_filter: Optional[str] = None,
    vector_name: Optional[str] = None,
    with_vectors: bool = False,
) -> str:
    """
    Performs a vector search in Qdrant.
    Args:
        query_vector_json (str): The query vector as a JSON string.
        collection_name (str): The name of the collection to search.
        top_k (int): The number of results to return.
        query_filter (str, optional): JSON string for the Qdrant filter object.
        vector_name (str, optional): Name of the vector field to use ('using' parameter).
        with_vectors (bool): Whether to return the stored vectors.
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

    # 3. Qdrant Search Arguments
    search_args = {
        "collection_name": collection_name,
        "query": query_vector,
        "limit": top_k,
        "with_payload": True,
        "with_vectors": with_vectors,
    }

    if qdrant_filter:
        search_args["query_filter"] = qdrant_filter

    if vector_name:
        search_args["using"] = vector_name

    # 4. Qdrant Search Execution
    try:
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
                result_dict["vector"] = hit.vector

            results.append(result_dict)

        logger.info(f"Successfully retrieved {len(results)} documents from Qdrant in collection {collection_name}")
        return json.dumps(results)

    except Exception as e:
        logger.error(f"Qdrant search failed: {e}", exc_info=True)
        return json.dumps({"error": "Qdrant Error", "message": f"Search failed in {collection_name}. Detail: {str(e)}"})

# --- Gradio Interface Setup ---
iface = gr.Interface(
    fn=query_points,
    inputs=[
        gr.Textbox(label="1. Query Vector (JSON Array)", placeholder="[0.1, 0.2, 0.3, ...]", lines=3),
        gr.Textbox(label="2. Collection Name", value=DEFAULT_COLLECTION_NAME),
        gr.Number(label="3. Top K Results", value=DEFAULT_TOP_K, step=1),
        gr.Textbox(label="4. Query Filter (JSON, optional)", placeholder='{"key":"value", "key 2":"[value1, value2]"}', lines=2),
        gr.Textbox(label="5. Vector Name (optional)", placeholder="leave blank for default"),
        gr.Checkbox(label="6. Return Vectors", value=False),
    ],
    outputs=gr.Json(label="API Response"),
    title="Private Qdrant Retrieval API (Powered by Gradio)",
    api_name="query_points",
    description="Vector search endpoint. All optional parameters are now named arguments in both the UI and external API calls."
)

# Launch the Gradio application
if __name__ == "__main__":
    logger.info("Gradio application starting...")
    iface.launch(server_name="0.0.0.0", server_port=7860)
