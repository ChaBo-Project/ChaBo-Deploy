import os
import sys
import time
import logging
import uuid
import numpy as np
from qdrant_client import QdrantClient, models
from datasets import load_dataset
from typing import List, Dict, Any
import ast

os.environ['HF_HOME'] = '/tmp/hf_cache'

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---
# Read configuration from environment variables set in the Dockerfile
QDRANT_HOST = os.getenv("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))

# Embeddings related params and config
HF_DATASET_NAME = os.getenv("EMBEDDING_DATASET")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "default_collection")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", 1024))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 200))
# Same "field:type,field:type" format as the orchestrator's [metadata_filters] filterable_fields
FILTERABLE_FIELDS = os.getenv("FILTERABLE_FIELDS", "")
# Optional hybrid retrieval: a fastembed sparse model id (e.g. Qdrant/bm25). Unset keeps
# today's collection: one unnamed dense vector. Set, the collection gets a named "dense"
# vector plus a BM25 "sparse" vector built from each payload's "text".
SPARSE_MODEL = os.getenv("SPARSE_MODEL", "").strip()
# Snowball stemmer/stopword language for SPARSE_MODEL (fastembed supports 18 languages).
SPARSE_LANGUAGE = os.getenv("SPARSE_LANGUAGE", "").strip() or "english"
HYBRID = bool(SPARSE_MODEL)
# Fixed vector names, read by the orchestrator from the collection metadata below
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
# Payload field the BM25 vectors are built from (the field the orchestrator returns as text)
SPARSE_TEXT_FIELD = "text"
# TOKENS AND SECRETS
embedding_token = os.getenv("DATASET_READ_TOKEN")
qdrant_token = os.getenv("QDRANT__SERVICE__API_KEY")


# --- Core Functions ---

def get_qdrant_client_raw() -> QdrantClient:
    """Initialize the raw Qdrant client."""
    # Connecting to the Qdrant server started locally via start.sh
    logger.info("Getting client connection")
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, api_key = qdrant_token, https =False)
    return client

def load_sparse_encoder():
    """Load the fastembed sparse encoder for SPARSE_MODEL; raises if it can't be loaded."""
    from fastembed import SparseTextEmbedding
    logger.info(f"Loading sparse encoder '{SPARSE_MODEL}' (language={SPARSE_LANGUAGE})...")
    return SparseTextEmbedding(model_name=SPARSE_MODEL, language=SPARSE_LANGUAGE)


def retrieval_recipe() -> Dict[str, Any]:
    """
    Collection metadata describing how the vectors were built. The orchestrator reads it
    at startup, so its query-side BM25 always matches the documents' encoding.
    """
    import fastembed
    return {"chabo_retrieval": {
        "version": 1,
        "dense": {"vector": DENSE_VECTOR_NAME, "size": EMBEDDING_DIMENSION},
        "sparse": {
            "vector": SPARSE_VECTOR_NAME,
            "model": SPARSE_MODEL,
            "language": SPARSE_LANGUAGE,
            "fastembed": fastembed.__version__,
            "text_field": SPARSE_TEXT_FIELD,
        },
    }}


def create_collection_if_not_exists(client: QdrantClient):
    """Checks if the collection exists and creates it if it doesn't."""

    # Check if collection is present
    collections = client.get_collections().collections
    if COLLECTION_NAME in [c.name for c in collections]:
        # An empty collection means an earlier start died before indexing (e.g. out of
        # memory); index into it instead of serving no data.
        if not client.get_collection(COLLECTION_NAME).points_count:
            logger.warning(
                f"Collection '{COLLECTION_NAME}' exists but is empty (an earlier start was "
                f"interrupted?). Indexing into it."
            )
            return False
        logger.info(f"Collection '{COLLECTION_NAME}' already exists. Skipping data loading.")
        if HYBRID:
            sparse = client.get_collection(COLLECTION_NAME).config.params.sparse_vectors or {}
            if SPARSE_VECTOR_NAME not in sparse:
                logger.warning(
                    f"SPARSE_MODEL is set but existing collection '{COLLECTION_NAME}' has no "
                    f"'{SPARSE_VECTOR_NAME}' vector; it stays dense-only. Use a new "
                    f"COLLECTION_NAME (or delete the collection) to rebuild it as hybrid."
                )
        return True # Collection found

    logger.info(f"Collection '{COLLECTION_NAME}' not found. Creating collection...")

    try:
        if HYBRID:
            # Named dense + BM25 sparse vector; IDF is maintained by Qdrant itself
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config={
                    DENSE_VECTOR_NAME: models.VectorParams(
                        size=EMBEDDING_DIMENSION,
                        distance=models.Distance.COSINE
                    )
                },
                sparse_vectors_config={
                    SPARSE_VECTOR_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
                metadata=retrieval_recipe(),
            )
            logger.info(
                f"Collection '{COLLECTION_NAME}' created for hybrid retrieval: "
                f"dense='{DENSE_VECTOR_NAME}' + sparse='{SPARSE_VECTOR_NAME}' "
                f"({SPARSE_MODEL}, {SPARSE_LANGUAGE})."
            )
            return False

        # Create the collection with the correct vector size
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=EMBEDDING_DIMENSION,
                distance=models.Distance.COSINE
            )
        )
        logger.info(f"Collection '{COLLECTION_NAME}' successfully created.")
        return False # Collection was created (needs data loading)

    except Exception as e:
        logger.error(f"Failed to create collection: {e}")
        return False

PAYLOAD_SCHEMA_BY_TYPE = {
    "str": models.PayloadSchemaType.KEYWORD,
    "list": models.PayloadSchemaType.KEYWORD,  # keyword index covers lists of strings
    "int": models.PayloadSchemaType.INTEGER,
}

def create_payload_indexes(client: QdrantClient):
    """Indexes each FILTERABLE_FIELDS entry under metadata.<field> so filtered search doesn't scan every point."""
    for item in FILTERABLE_FIELDS.split(","):
        item = item.strip()
        if not item:
            continue
        # A field without a type defaults to str, as in the orchestrator
        field, _, field_type = item.partition(":")
        field, field_type = field.strip(), (field_type.strip() or "str")

        schema = PAYLOAD_SCHEMA_BY_TYPE.get(field_type)
        if schema is None:
            logger.warning(f"Skipping payload index for '{field}': unsupported type '{field_type}' (use str, int or list).")
            continue

        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=f"metadata.{field}",
            field_schema=schema,
        )
        logger.info(f"Payload index on 'metadata.{field}' ({field_type}) is ready.")

def safe_parse_data(data: Any, expected_type: type, field_name: str, index: int) -> Any:
    """
    Safely parses input data, converting strings to the expected type (list/dict)
    using ast.literal_eval if necessary, or raises an error.
    """
    if isinstance(data, expected_type):
        return data

    if isinstance(data, str):
        try:
            parsed_data = ast.literal_eval(data)
            if isinstance(parsed_data, expected_type):
                return parsed_data
            else:
                raise ValueError(f"Parsed type is {type(parsed_data)}, not expected {expected_type}")
        except (ValueError, SyntaxError, TypeError) as e:
            raise ValueError(
                f"Data parsing error for {field_name} at index {index}: "
                f"Input string '{data[:50]}...' is not a valid {expected_type.__name__} string. Error: {e}"
            )

    raise TypeError(
        f"Data type error for {field_name} at index {index}: "
        f"Expected {expected_type.__name__} or string representation, but got {type(data).__name__}"
    )


def process_data_item(index: int, item: dict, vector_column_name: str, vector_size: int):
    """
    Helper function to safely parse, validate, and create a single PointStruct.
    Returns the PointStruct object or None if an error occurs.
    """
    try:
        # 1. Parsing Vector (List[float])
        vector = safe_parse_data(
            item.get(vector_column_name),
            list,
            vector_column_name,
            index
        )

        # 2. Parsing Payload (Dict[str, Any])
        payload = safe_parse_data(
            item.get('payload'),
            dict,
            'payload',
            index
        )

        # 3. Validation
        if len(vector) != EMBEDDING_DIMENSION:
            # Raise ValueError so it is caught and logged below
            raise ValueError(f"Vector at index {index} has incorrect size: {len(vector)} (Expected: {EMBEDDING_DIMENSION})")

        # 4. Create Qdrant PointStruct
        return models.PointStruct(
            id=item['id'],  # Assuming 'id' field is correctly available in the item
            vector=vector,
            payload=payload
        )

    except (ValueError, TypeError) as e:
        # Log the error and allow the main loop to skip this item
        print(f"SKIPPING item at index {index} due to validation/parsing error: {e}")
        return None

def add_sparse_vectors(points: List[models.PointStruct], encoder) -> None:
    """Turn each point's dense vector into {dense, sparse}, BM25-encoding its payload text."""
    texts = [str((p.payload or {}).get(SPARSE_TEXT_FIELD) or "") for p in points]
    for point, embedding in zip(points, encoder.embed(texts)):
        point.vector = {
            DENSE_VECTOR_NAME: point.vector,
            SPARSE_VECTOR_NAME: models.SparseVector(
                indices=[int(i) for i in embedding.indices],
                values=[float(v) for v in embedding.values],
            ),
        }


def load_and_index_data(client: QdrantClient, sparse_encoder=None) -> bool:
    """Pulls pre-embedded data from HF and indexes it into Qdrant using client.upsert.
    With a sparse encoder, also adds a BM25 vector per point (hybrid collection).
    Returns False if indexing failed or no point was indexed."""
    logger.info(f"Starting data loading from Hugging Face dataset: {HF_DATASET_NAME}")


    try:
        # Load a small slice of the dataset for demonstration/speed
        dataset = load_dataset(HF_DATASET_NAME, token = embedding_token, cache_dir="/tmp/datasets_cache")
        dataset = dataset['train']
        vector_column_name = os.getenv("VECTOR_COLUMN_NAME", "vector")
        logger.info(f"Loaded {len(dataset)} documents. Indexing in batches of {BATCH_SIZE}...")
        logger.info(f"Dataset id of type {type(dataset[0]['id'])}")
        logger.info(f"Dataset vector of type {type(dataset[0][vector_column_name])}")
        logger.info(f"Dataset payload of type {type(dataset[0]['payload'])}")

        def upsert_batch(batch: List[models.PointStruct]) -> None:
            if sparse_encoder is not None:
                add_sparse_vectors(batch, sparse_encoder)
            client.upsert(collection_name=COLLECTION_NAME, points=batch)

        # Build and upsert one batch at a time: holding every point in memory first
        # needs ~33 KB per 1024-dim vector as Python floats (~17 GB for 500k points).
        batch: List[models.PointStruct] = []
        upserted = 0
        for i, item in enumerate(dataset):
            point = process_data_item(i, item, vector_column_name, EMBEDDING_DIMENSION)
            if point is not None:
                batch.append(point)
            if len(batch) >= BATCH_SIZE:
                upsert_batch(batch)
                upserted += len(batch)
                batch = []
                if upserted % (BATCH_SIZE * 100) == 0:
                    logger.info(f"Upserted {upserted}/{len(dataset)} points...")
        if batch:
            upsert_batch(batch)
            upserted += len(batch)

        if not upserted:
            logger.error("Data indexing failed: no valid points in the dataset (see SKIPPING lines above).")
            return False
        logger.info(f"Data Upsert and indexing complete: {upserted} points.")
        return True

    except Exception as e:
        logger.error(f"Data indexing failed. Check dataset structure or network access. Error: {e}")
        return False


def main_initialization() -> bool:
    """Main execution block. Returns False if the collection could not be made ready."""
    max_retries = 10

    # Wait for the local Qdrant server (started by start.sh) to become ready
    for attempt in range(max_retries):
        try:
            client = get_qdrant_client_raw()
            # Simple check to see if the server is responsive
            client.get_collections()
            logger.info("Qdrant server is reachable.")
            break
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries}: Qdrant not ready yet. Retrying in 5 seconds. ({e})")
            time.sleep(5)
    else:
        logger.error("Qdrant server failed to start within the maximum retry limit. Exiting initialization.")
        return False

    # Load the sparse encoder before creating anything, so a bad SPARSE_MODEL /
    # SPARSE_LANGUAGE leaves no half-built hybrid collection behind.
    sparse_encoder = None
    if HYBRID:
        try:
            sparse_encoder = load_sparse_encoder()
        except Exception as e:
            logger.error(
                f"Cannot load sparse encoder SPARSE_MODEL='{SPARSE_MODEL}' "
                f"SPARSE_LANGUAGE='{SPARSE_LANGUAGE}': {e}. Fix or unset SPARSE_MODEL. "
                f"Skipping collection setup."
            )
            return False

    # Check if the collection exists (meaning data was previously indexed)
    collection_exists = create_collection_if_not_exists(client)

    # If the collection was just created (or if it never existed), load the data
    if not collection_exists:
        # Indexes go in before the upload, so they are built as points arrive
        create_payload_indexes(client)
        # Dense vectors come pre-computed in the dataset; BM25 is computed here if hybrid
        return load_and_index_data(client, sparse_encoder)
    logger.info("Collection already populated, skipping data indexing.")
    return True


if __name__ == "__main__":
    # Non-zero exit makes start.sh stop instead of serving an empty collection
    sys.exit(0 if main_initialization() else 1)
