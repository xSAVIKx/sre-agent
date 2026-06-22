"""Management and seeding of diagnostic itinerary templates.
"""

import os
import logging
from typing import Any
from google import genai
from google.genai import types
from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector
from google.cloud.firestore_v1.base_query import DistanceMeasure
from sre_agent.config import IS_MOCK
from sre_common import retry_async

logger = logging.getLogger("sre_agent.itinerary")

DEFAULT_TEMPLATES = [
    {
        "name": "Cloud Run Revision Diagnostic Template",
        "description": "Standard diagnostic templates for Cloud Run services, including container CPU and memory utilization metric queries and error/warning log queries.",
        "resource_type": "cloud_run_revision",
        "helpers": {
            "metrics": 'metric.type="run.googleapis.com/container/cpu/utilizations" AND resource.labels.service_name="{service_name}"',
            "logs": 'resource.type="cloud_run_revision" AND resource.labels.service_name="{service_name}" AND severity>=WARNING'
        }
    },
    {
        "name": "Cloud SQL Database Diagnostic Template",
        "description": "Standard diagnostic templates for Cloud SQL databases, including database connection counts, CPU utilization, and system error log queries.",
        "resource_type": "cloudsql_database",
        "helpers": {
            "metrics": 'metric.type="cloudsql.googleapis.com/database/postgresql/connection_count" AND resource.labels.database_id="{database_id}"',
            "logs": 'resource.type="cloudsql_database" AND resource.labels.database_id="{database_id}" AND severity>=WARNING'
        }
    },
    {
        "name": "Datastore/Firestore Database Diagnostic Template",
        "description": "Standard diagnostic templates for Firestore/Datastore databases, including read/write latencies, document operations rate, and partition health queries.",
        "resource_type": "datastore_database",
        "helpers": {
            "metrics": 'metric.type="firestore.googleapis.com/document/read_ops_count" AND resource.labels.database_id="{database_id}"',
            "logs": 'resource.type="datastore_database" AND resource.labels.database_id="{database_id}" AND severity>=WARNING'
        }
    }
]

@retry_async(max_retries=3, initial_delay=1.0)
async def get_embedding(text: str) -> list[float]:
    """Generates the text embedding using gemini-embedding-2.
    
    If in mock mode, returns a dummy zero vector of size 768.
    """
    if IS_MOCK:
        return [0.0] * 768
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY environment variable not set. Returning zero vector.")
            return [0.0] * 768
            
        client = genai.Client(api_key=api_key)
        response = await client.aio.models.embed_content(
            model="gemini-embedding-2",
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        return response.embeddings[0].values
    except Exception as e:
        logger.error(f"Failed to generate embedding via gemini-embedding-2: {e}")
        return [0.0] * 768

@retry_async(max_retries=3, initial_delay=1.0)
async def seed_templates_if_empty(db: firestore.AsyncClient) -> None:
    """Queries Firestore to check if the itinerary_templates collection is empty.
    If so, seeds it with default templates and generated embeddings.
    """
    if db is None:
        logger.info("Firestore client is None (mock mode or initialization error). Skipping seeding.")
        return
        
    try:
        collection_ref = db.collection("itinerary_templates")
        docs = await collection_ref.limit(1).get()
        if len(docs) > 0:
            logger.info("itinerary_templates collection is already seeded.")
            return
            
        logger.info("itinerary_templates collection is empty. Seeding default templates...")
        for template in DEFAULT_TEMPLATES:
            embedding_vals = await get_embedding(template["description"])
            doc_data = {
                "name": template["name"],
                "description": template["description"],
                "embedding": Vector(embedding_vals),
                "resource_type": template["resource_type"],
                "helpers": template["helpers"]
            }
            doc_id = template["resource_type"]
            await collection_ref.document(doc_id).set(doc_data)
            logger.info(f"Seeded template for resource_type: {doc_id}")
            
        logger.info("Completed seeding itinerary_templates.")
    except Exception as e:
        logger.error(f"Error seeding itinerary_templates in Firestore: {e}")

@retry_async(max_retries=3, initial_delay=1.0)
async def find_matching_template(db: firestore.AsyncClient | None, resource_type: str, query_text: str) -> dict[str, Any] | None:
    """Finds the single best matching template using Firestore vector search.
    
    If db is None or IS_MOCK is True, falls back to string/regex matching.
    """
    from typing import Any
    
    if IS_MOCK or db is None:
        logger.info(f"[Mock Match] Finding local template for resource_type: {resource_type}")
        for template in DEFAULT_TEMPLATES:
            if template["resource_type"] == resource_type:
                return template
        return None
        
    try:
        # Generate query embedding
        query_vector = await get_embedding(query_text)
        
        # Execute vector search with pre-filtering
        query = db.collection("itinerary_templates") \
            .where("resource_type", "==", resource_type) \
            .find_nearest(
                vector_field="embedding",
                query_vector=Vector(query_vector),
                distance_measure=DistanceMeasure.COSINE,
                limit=1
            )
        
        docs = await query.get()
        if docs:
            doc = docs[0]
            doc_data = doc.to_dict()
            logger.info(f"Firestore vector search matched template: {doc.id}")
            return doc_data
    except Exception as e:
        logger.error(f"Error during Firestore vector search: {e}. Falling back to default templates matching.")
        # Fallback to local
        for template in DEFAULT_TEMPLATES:
            if template["resource_type"] == resource_type:
                return template
                
    return None

