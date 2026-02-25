Good plan. Here's how I'd approach this — your existing worker keeps its role of receiving `embed_pdfs` from Redis, but instead of doing the work itself, it dispatches individual document embedding jobs to Celery. Celery runs 4 at a time via concurrency setting.

**The architecture:**

```
Redis (python_tasks queue)
  └─ 'embed_pdfs' job
       └─ your existing worker receives it
            └─ fetches doc IDs, dispatches N celery tasks (one per doc)
                 └─ Celery workers (concurrency=4) each embed one doc
```

Here's the implementation — two files:

**`celery_app.py`** — Celery app + the actual embedding task:

```python
import os
import numpy as np
import psycopg2
from celery import Celery
from sentence_transformers import SentenceTransformer
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
    global _model
    if _model is None:
        logger.info("Loading SentenceTransformer model...")
        _model = SentenceTransformer('all-MiniLM-L6-v2')
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
    soft_time_limit=120,   # raises SoftTimeLimitExceeded after 2min (nice-to-have)
    time_limit=150,         # hard kill after 2.5min
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
```

**Changes to your existing `worker.py`** — replace the `embed_pdfs()` function and add an import:

```python
# Add at top
from celery import group
from celery_app import embed_single_document

def embed_pdfs():
    """
    Dispatch one Celery task per unembedded document.
    Celery handles the concurrency (run workers with --concurrency=4).
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT id, raw_text FROM documents WHERE embedding IS NULL")
    documents = cursor.fetchall()
    cursor.close()
    conn.close()

    if not documents:
        print("No documents to embed.")
        return

    print(f"Dispatching {len(documents)} embedding tasks to Celery...")
    
    # Build a group so you can optionally wait/inspect results
    job = group(
        embed_single_document.s(doc_id, raw_text)
        for doc_id, raw_text in documents
        if raw_text  # skip empties
    )
    result = job.apply_async()
    
    # Optional: block and wait for all to finish before marking step complete
    # results = result.get(timeout=600)  # 10min overall timeout
    # print(f"All done: {results}")
    
    print(f"All {len(documents)} tasks dispatched. Celery is processing...")
```

**Start your Celery workers:**

```bash
celery -A celery_app worker \
  --queues=embed_tasks \
  --concurrency=4 \
  --loglevel=info
```

**Key design decisions worth noting:**

- The model loads **once per worker process**, not per task — this is critical for performance since `SentenceTransformer` init is slow.
- `soft_time_limit` / `time_limit` give you the timeout nice-to-have with graceful vs hard kill behavior.
- `autoretry_for=(Exception,)` with `retry_backoff=True` gives you 3 retries with exponential backoff on any failure.
- Your existing Redis-based worker loop is completely unchanged except for the `embed_pdfs()` swap — it still pops `embed_pdfs` from `python_tasks` and now just fans out to Celery instead of doing the work inline.
- The `apply_async()` call is non-blocking, so your main worker loop returns immediately. If you want to block until all embeddings are done before proceeding (e.g., to trigger `umap` next), uncomment the `result.get(timeout=600)` line.

Yes, exactly — a separate container is the right call. Celery workers are long-running processes, so they don't belong in your existing task-dispatching container.

Here's what to add to your `docker-compose.yml`:

```yaml
celery-embedder:
  build: .  # same Dockerfile as your existing python worker
  command: celery -A celery_app worker --queues=embed_tasks --concurrency=4 --loglevel=info
  environment:
    - REDIS_URL=redis://redis:6379
  depends_on:
    - redis
    - postgres
  # optional: restart if it crashes
  restart: unless-stopped
```

That's really it. Since it's the same codebase, same `Dockerfile`, same Redis and Postgres — you just override the `command`. No new image needed.

A few things to be aware of:

**Memory** — `SentenceTransformer` with 4 concurrent workers means the model loads once per process. With `--concurrency=4` using the default prefork pool, that's 4 separate processes each loading the model. `all-MiniLM-L6-v2` is ~90MB so it's fine, but if you ever switch to a larger model, consider dropping concurrency or switching to `--pool=threads` (though threads won't give you true parallelism for CPU-bound work).

**Concurrency model** — embedding is CPU-bound, so prefork (the default) is correct here. Don't use `--pool=gevent` or `--pool=eventlet` for this.

**Scaling later** — if you want more than 4 parallel embeddings, you can either bump `--concurrency` or just `scale` the service:
```bash
docker compose up --scale celery-embedder=2
```
That gives you 8 workers total with no config changes.

**Healthcheck** — Celery doesn't expose an HTTP port, but you can add a basic healthcheck if your compose setup needs one:
```yaml
healthcheck:
  test: ["CMD", "celery", "-A", "celery_app", "inspect", "ping"]
  interval: 30s
  timeout: 10s
  retries: 3
```