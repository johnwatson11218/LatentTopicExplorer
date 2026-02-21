This is actually a **great idea** and a well-established pattern! What you're describing is essentially a **task queue** or **job queue** architecture with **worker processes**. Let me break down what you should know:

## The Pattern You're Describing

This is called:
- **Task Queue Pattern** / **Job Queue Pattern**
- **Producer-Consumer Pattern**
- **Message-Driven Architecture**
- Sometimes called **Background Job Processing**

Your instinct to use Redis for this is spot-on, but there are better tools built specifically for this use case.

## Better Frameworks (Don't Reinvent the Wheel!)

### **Celery** (Most Popular for Python)
This is the de facto standard for Python task queues and works perfectly with Redis:

```python
# Producer
from celery import Celery

app = Celery('nlp_tasks', broker='redis://localhost:6379')

@app.task
def import_pdf(file_path):
    # Import logic
    return document_id

@app.task
def embed_document(doc_id):
    # Embedding logic
    pass

# Chain tasks together
from celery import chain
workflow = chain(
    import_pdf.s('/path/to/doc.pdf'),
    embed_document.s()
)
workflow.apply_async()
```

**Pros:**
- Battle-tested, mature ecosystem
- Built-in retries, error handling, result backends
- Task chaining, groups, and workflows (celery.canvas)
- Monitoring tools (Flower)
- Supports Redis, RabbitMQ, and others

**Cons:**
- Can be overkill for simple use cases
- Configuration can be complex

### **RQ (Redis Queue)** (Simpler Alternative)
Lighter-weight, Redis-only option:

```python
from redis import Redis
from rq import Queue

redis_conn = Redis()
q = Queue(connection=redis_conn)

# Enqueue jobs
job = q.enqueue('tasks.import_pdf', '/path/to/doc.pdf')

# Chain tasks
from rq import Callback
q.enqueue('tasks.import_pdf', '/path/to/doc.pdf',
          on_success=Callback('tasks.embed_document'))
```

**Pros:**
- Much simpler than Celery
- Easy to understand and debug
- Good enough for most use cases
- Nice web UI (rq-dashboard)

**Cons:**
- Less feature-rich than Celery
- Redis-only (not necessarily a con for you)

### **Dramatiq** (Modern Middle Ground)
Newer alternative with good design:

```python
import dramatiq

@dramatiq.actor
def import_pdf(file_path):
    # Import logic
    return document_id

@dramatiq.actor
def embed_document(doc_id):
    # Embedding logic
    pass

# Pipeline
import_pdf.send('/path/to/doc.pdf') | embed_document.send()
```

**Pros:**
- Simpler than Celery, more robust than RQ
- Better API design
- Built-in rate limiting, retries
- Works with Redis and RabbitMQ

## Your Specific Use Case

For your PDF pipeline, here's how I'd architect it:

### **Recommended: Use Celery with Canvas (Workflows)**

```python
from celery import Celery, chain, group, chord
from celery.result import allow_join_result

app = Celery('nlp_pipeline', broker='redis://localhost:6379')

# Individual tasks
@app.task(bind=True, max_retries=3)
def import_pdf(self, file_path):
    try:
        doc_id = extract_and_save(file_path)
        return doc_id
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)

@app.task
def embed_document(doc_id):
    chunks = get_chunks(doc_id)
    embeddings = generate_embeddings(chunks)
    save_embeddings(doc_id, embeddings)
    return doc_id

@app.task
def calculate_document_embedding(doc_id):
    avg_embedding = average_chunk_embeddings(doc_id)
    save_doc_embedding(doc_id, avg_embedding)
    return doc_id

# Collective tasks (wait for all docs)
@app.task
def run_umap(doc_ids):
    all_embeddings = get_all_embeddings(doc_ids)
    reduced = umap_transform(all_embeddings)
    save_reduced(reduced)
    return doc_ids

@app.task
def run_clustering(doc_ids):
    reduced_embeddings = get_reduced_embeddings(doc_ids)
    clusters = hdbscan_cluster(reduced_embeddings)
    save_clusters(clusters)
    return doc_ids

@app.task
def extract_terms(doc_ids):
    for doc_id in doc_ids:
        terms = spacy_extract(doc_id)
        save_terms(doc_id, terms)
    return doc_ids

@app.task
def calculate_tfidf(doc_ids):
    tfidf_scores = compute_tfidf(doc_ids)
    save_tfidf(tfidf_scores)
    return "Complete!"

# Orchestration
def process_corpus(pdf_directory):
    pdf_files = scan_directory(pdf_directory)
    
    # Step 1: Import and embed all PDFs in parallel
    import_and_embed = group(
        chain(import_pdf.s(pdf), embed_document.s(), calculate_document_embedding.s())
        for pdf in pdf_files
    )
    
    # Step 2: Once ALL are done, run collective operations
    workflow = chord(import_and_embed)(
        chain(
            run_umap.s(),
            run_clustering.s(),
            extract_terms.s(),
            calculate_tfidf.s()
        )
    )
    
    return workflow.apply_async()
```

### Key Concepts You're Using:

1. **Tasks**: Individual units of work (`import_pdf`, `embed_document`)
2. **Chains**: Sequential tasks where output of one feeds into next
3. **Groups**: Run multiple tasks in parallel
4. **Chords**: Run group in parallel, then execute callback when ALL complete (perfect for your UMAP/clustering steps!)
5. **Retries**: Automatic retry with exponential backoff
6. **Result Backends**: Track task status and results

## Monitoring & Observability

Use **Flower** for Celery monitoring:
```bash
pip install flower
celery -A nlp_pipeline flower
```

This gives you a web dashboard showing:
- Active/completed/failed tasks
- Worker status
- Task execution times
- Retry attempts

## Alternative: Workflow Orchestration Tools

If you want something even more robust, consider:

### **Prefect** (Modern workflow orchestration)
```python
from prefect import flow, task

@task(retries=3)
def import_pdf(file_path):
    return extract_and_save(file_path)

@task
def embed_document(doc_id):
    return generate_embeddings(doc_id)

@flow
def pdf_pipeline(pdf_files):
    doc_ids = []
    for pdf in pdf_files:
        doc_id = import_pdf(pdf)
        embed_document(doc_id)
        doc_ids.append(doc_id)
    
    # Wait for all, then run collective tasks
    run_umap(doc_ids)
    run_clustering(doc_ids)
```

**Pros:**
- Beautiful UI
- Better observability
- Easier to debug
- Modern Python-first design

**Cons:**
- Might be overkill for your use case
- Requires separate Prefect server

### **Apache Airflow** (Enterprise-grade)
Overkill for your project, but worth knowing about for future reference.

## My Recommendation for You

**Use Celery** because:
1. You already have Redis
2. It handles exactly your use case (parallel processing + waiting for all to complete)
3. `chord` pattern is perfect for "process all PDFs, then run collective analysis"
4. Mature ecosystem with great monitoring
5. Easy to add retries and error handling
6. Free web UI with Flower

**Implementation Strategy:**
1. Replace your 6 manual URLs with a single `/process_corpus` endpoint
2. That endpoint scans the folder and creates a Celery workflow
3. Workers pick up tasks automatically
4. Use `chord` to wait for all individual documents before collective operations
5. Monitor progress via Flower dashboard
6. Store results in PostgreSQL as tasks complete

This pattern is **exactly** what task queues were designed for, and you'll save yourself tons of headache by using an established framework rather than building message passing from scratch with raw Redis.

Want me to help you set up a concrete Celery configuration for your project?