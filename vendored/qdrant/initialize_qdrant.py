import os
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

def create_collection_if_not_exists(client: QdrantClient):
    """Checks if the collection exists and creates it if it doesn't."""

    # Check if collection is present
    collections = client.get_collections().collections
    if COLLECTION_NAME in [c.name for c in collections]:
        logger.info(f"Collection '{COLLECTION_NAME}' already exists. Skipping data loading.")
        return True # Collection found

    logger.info(f"Collection '{COLLECTION_NAME}' not found. Creating collection...")

    try:
        # Create the collection with the correct vector size
        client.recreate_collection(
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

def load_and_index_data(client: QdrantClient):
    """Pulls pre-embedded data from HF and indexes it into Qdrant using client.upsert."""
    logger.info(f"Starting data loading from Hugging Face dataset: {HF_DATASET_NAME}")


    try:
        # Load a small slice of the dataset for demonstration/speed
        dataset = load_dataset(HF_DATASET_NAME, token = embedding_token, cache_dir="/tmp/datasets_cache")
        dataset = dataset['train']
        points_to_upsert: List[models.PointStruct] = []
        vector_column_name = os.getenv("VECTOR_COLUMN_NAME", "vector")
        logger.info(f"Loaded {len(dataset)} documents. Preparing points for indexing...")
        logger.info(f"Dataset id of type {type(dataset[0]['id'])}")
        logger.info(f"Dataset vector of type {type(dataset[0][vector_column_name])}")
        logger.info(f"Dataset payload of type {type(dataset[0]['payload'])}")

        points_to_upsert = [
            point
            for i, item in enumerate(dataset)
            if (point := process_data_item(i, item, vector_column_name, EMBEDDING_DIMENSION)) is not None
        ]

        print(f"Successfully prepared {len(points_to_upsert)} points for upsert.")


        for i in range(0, len(points_to_upsert), BATCH_SIZE):
            client.upsert(
                collection_name=COLLECTION_NAME,
                points=points_to_upsert[i:i+BATCH_SIZE]
            )
        logger.info("Data Upsert and indexing complete.")

    except Exception as e:
        logger.error(f"Data indexing failed. Check dataset structure or network access. Error: {e}")


def main_initialization():
    """Main execution block."""
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
        return

    # Check if the collection exists (meaning data was previously indexed)
    collection_exists = create_collection_if_not_exists(client)

    # If the collection was just created (or if it never existed), load the data
    if not collection_exists:
        # We no longer need to initialize an embedding model
        load_and_index_data(client)
    else:
        logger.info("Collection already populated, skipping data indexing.")


if __name__ == "__main__":
    main_initialization()
