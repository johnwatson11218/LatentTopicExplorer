import os
import numpy as np
import psycopg2
from celery import Celery
#from sentence_transformers import SentenceTransformer
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379')
DB_CONFIG = {
    "dbname": "second_brain",
    "user": "postgres",
    "password": "test_case",
    "host": "postgres",
    "port": 5432
}

# Celery listens on a dedicated queue so it doesn't conflict with your existing worker
app = Celery('embeddings', broker=REDIS_URL, backend=REDIS_URL)
app.conf.task_queues_default_exchange = 'celery'
app.conf.task_default_queue = 'embed_tasks'
app.conf.task_serializer = 'json'
app.conf.result_serializer = 'json'
app.conf.accept_content = ['json']

# Lazy-load model once per worker process (not per task)
_model = None

def get_model():
    from sentence_transformers import SentenceTransformer
    global _model
    if _model is None:
        logger.info("Loading SentenceTransformer model...")
        #_model = SentenceTransformer('all-MiniLM-L6-v2')
        
        
        import torch
        # Check for CUDA availability and set device
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        print(f"Using device: {device}")

        # Load model directly onto the specified device
        _model = SentenceTransformer('all-MiniLM-L6-v2', device=device)
    return _model


def chunk_text(text: str, chunk_size: int = 100, overlap: int = 10):
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    step = chunk_size - overlap
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return chunks


def insert_chunked_embeddings(conn, document_id, chunks, embeddings):
    cursor = conn.cursor()
    for seq_num, (chunk, embedding) in enumerate(zip(chunks, embeddings), 1):
        cursor.execute("""
            INSERT INTO chunked_embeddings (input_text, embedding, sequence_number, document_id)
            VALUES (%s, %s, %s, %s)
        """, (chunk, embedding.tolist(), seq_num, document_id))
    cursor.close()


def update_doc_embedding(conn, document_id, embedding):
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE documents SET embedding = %s WHERE id = %s",
        (embedding.tolist(), document_id)
    )
    cursor.close()


@app.task(
    bind=True,
    name='embed_single_document',
    queue='embed_tasks',
    max_retries=3,
    soft_time_limit=1200,   # raises SoftTimeLimitExceeded after 2min (nice-to-have)
    time_limit=1500,         # hard kill after 2.5min
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def embed_single_document(self, document_id: int, raw_text: str):
    """Embed one document: chunk → encode → insert chunks → update doc embedding."""
    logger.info(f"Embedding document {document_id}")
    
    model = get_model()
    chunks = chunk_text(raw_text, chunk_size=100, overlap=10)
    
    embeddings = model.encode(chunks)  # np array shape (n_chunks, dim)
    doc_embedding = np.mean(embeddings, axis=0)

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        insert_chunked_embeddings(conn, document_id, chunks, embeddings)
        update_doc_embedding(conn, document_id, doc_embedding)
        conn.commit()
        logger.info(f"Document {document_id} done — {len(chunks)} chunks")
    except Exception as e:
        conn.rollback()
        raise  # let Celery retry
    finally:
        conn.close()
    
    return {"document_id": document_id, "chunks": len(chunks)}