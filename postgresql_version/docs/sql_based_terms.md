Brilliant! You're thinking in exactly the right direction. Let me show you how this would work:

## The Architecture: Page-Level Term Processing with SQL Aggregation

### 1. **Process Terms at Page Level (Immediately After Extraction)**

```python
def process_page_terms(page_id: int, document_id: int, conn):
    """
    Process terms for a single page immediately after text extraction.
    Updates page_terms table (new) and document_terms (aggregated later).
    """
    cur = conn.cursor()
    
    # Get page text
    cur.execute("SELECT extracted_text FROM pages WHERE id = %s", (page_id,))
    result = cur.fetchone()
    if not result or not result[0]:
        return
    
    text = result[0]
    tokens = clean_and_tokenize(text)  # Stop words filtered here
    if not tokens:
        return
    
    # Count terms on this page
    term_counts = Counter(tokens)
    
    # Insert into page_terms (new table)
    rows = []
    for term, count in term_counts.items():
        # Get or create term
        cur.execute(
            "INSERT INTO terms (term) VALUES (%s) ON CONFLICT (term) DO NOTHING",
            (term,)
        )
        cur.execute("SELECT id FROM terms WHERE term = %s", (term,))
        term_id = cur.fetchone()[0]
        rows.append((page_id, term_id, count))
    
    if rows:
        execute_values(
            cur,
            """
            INSERT INTO page_terms (page_id, term_id, count)
            VALUES %s
            ON CONFLICT (page_id, term_id) DO UPDATE SET
                count = EXCLUDED.count
            """,
            rows
        )
    
    conn.commit()
```

### 2. **New Page-Level Terms Table**

```sql
-- New table for page-level term storage
CREATE TABLE page_terms (
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    term_id INTEGER NOT NULL REFERENCES terms(id),
    count INTEGER NOT NULL,  -- term frequency on this page
    PRIMARY KEY (page_id, term_id)
);

CREATE INDEX idx_page_terms_page_id ON page_terms(page_id);
CREATE INDEX idx_page_terms_term_id ON page_terms(term_id);
```

### 3. **SQL Aggregation to Document Terms**

This is the key insight - let SQL do the heavy lifting!

```sql
-- Aggregate page terms to document terms using SQL
INSERT INTO document_terms (document_id, term_id, raw_count, page_count, tf)
SELECT 
    p.document_id,
    pt.term_id,
    SUM(pt.count) as raw_count,
    COUNT(DISTINCT p.id) as page_count,
    0 as tf  -- placeholder, we'll update this
FROM page_terms pt
JOIN pages p ON p.id = pt.page_id
WHERE p.document_id = ANY(%s)  -- Process in batches
GROUP BY p.document_id, pt.term_id
ON CONFLICT (document_id, term_id) DO UPDATE SET
    raw_count = EXCLUDED.raw_count,
    page_count = EXCLUDED.page_count;

-- Update TF scores after aggregation
UPDATE document_terms dt
SET tf = dt.raw_count / sub.total_tokens
FROM (
    SELECT document_id, SUM(raw_count) as total_tokens
    FROM document_terms
    WHERE document_id = ANY(%s)
    GROUP BY document_id
) sub
WHERE dt.document_id = sub.document_id;
```

### 4. **Batch Processing with PostgreSQL Queues**

Leverage your existing `pipeline_queue` for page-level term processing:

```python
def enqueue_page_terms(conn):
    """Add page_terms processing to the queue."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pipeline_queue (page_id, step, status)
        SELECT p.id, 'page_terms', 'pending'
        FROM pages p
        WHERE p.extracted_text IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM pipeline_queue pq
            WHERE pq.page_id = p.id
            AND pq.step = 'page_terms'
            AND pq.status = 'done'
        )
        AND NOT EXISTS (
            SELECT 1 FROM page_terms pt
            WHERE pt.page_id = p.id
        )
        ON CONFLICT (page_id, step) DO NOTHING
    """)
    conn.commit()

def process_page_terms_batch(conn, batch_size: int = 100):
    """Process terms for a batch of pages."""
    page_ids = claim_batch(conn, 'page_terms', batch_size)
    
    for page_id in page_ids:
        try:
            # Get document_id for this page
            cur = conn.cursor()
            cur.execute("SELECT document_id FROM pages WHERE id = %s", (page_id,))
            doc_id = cur.fetchone()[0]
            
            process_page_terms(page_id, doc_id, conn)
            mark_done(conn, page_id, 'page_terms')
            
        except Exception as e:
            logging.error(f"Failed to process terms for page {page_id}: {e}")
            mark_error(conn, page_id, 'page_terms', str(e))
            continue
    
    conn.commit()

def aggregate_document_terms(conn, batch_size: int = 50):
    """
    Aggregate page_terms to document_terms using SQL.
    Process in batches of documents.
    """
    cur = conn.cursor()
    
    # Get documents that need aggregation
    cur.execute("""
        SELECT DISTINCT p.document_id
        FROM pages p
        WHERE EXISTS (
            SELECT 1 FROM page_terms pt
            WHERE pt.page_id = p.id
        )
        AND NOT EXISTS (
            SELECT 1 FROM document_terms dt
            WHERE dt.document_id = p.document_id
        )
        LIMIT %s
    """, (batch_size,))
    
    doc_ids = [r[0] for r in cur.fetchall()]
    
    if not doc_ids:
        return 0
    
    # Aggregate in SQL
    cur.execute("""
        INSERT INTO document_terms (document_id, term_id, raw_count, page_count, tf)
        WITH aggregated AS (
            SELECT 
                p.document_id,
                pt.term_id,
                SUM(pt.count) as raw_count,
                COUNT(DISTINCT p.id) as page_count
            FROM page_terms pt
            JOIN pages p ON p.id = pt.page_id
            WHERE p.document_id = ANY(%s)
            GROUP BY p.document_id, pt.term_id
        ),
        totals AS (
            SELECT document_id, SUM(raw_count) as total_tokens
            FROM aggregated
            GROUP BY document_id
        )
        SELECT 
            a.document_id,
            a.term_id,
            a.raw_count,
            a.page_count,
            a.raw_count::float / t.total_tokens as tf
        FROM aggregated a
        JOIN totals t ON t.document_id = a.document_id
        ON CONFLICT (document_id, term_id) DO UPDATE SET
            raw_count = EXCLUDED.raw_count,
            page_count = EXCLUDED.page_count,
            tf = EXCLUDED.tf
    """, (doc_ids,))
    
    conn.commit()
    return len(doc_ids)
```

### 5. **Stop Word Filtering at Page Level (Cached)**

Since you mentioned caching for stop words:

```python
from functools import lru_cache
import re
from nltk.corpus import stopwords
from nltk.stem import PorterStemmer

# Initialize once
nltk.download('stopwords', quiet=True)
nltk.download('punkt', quiet=True)

STOP_WORDS = set(stopwords.words('english'))
stemmer = PorterStemmer()

@lru_cache(maxsize=10000)
def stem_cached(token: str) -> str:
    """Cache stemmed tokens for performance."""
    return stemmer.stem(token)

@lru_cache(maxsize=1000)
def clean_and_tokenize_cached(text: str) -> tuple:
    """Cache tokenized text results."""
    tokens = re.findall(r'\b[a-z]{2,}\b', text.lower())
    result = [stem_cached(t) for t in tokens if t not in STOP_WORDS]
    return tuple(result)  # Make hashable for caching

def clean_and_tokenize(text: str) -> List[str]:
    """Public interface with caching."""
    return list(clean_and_tokenize_cached(text))
```

### 6. **Updated Pipeline Steps**

```python
def run_pipeline_step(conn, step_name: str, batch_size: int):
    """Run a single pipeline step with proper batching."""
    
    if step_name == 'page_terms':
        # Process terms at page level
        while True:
            batch = claim_batch(conn, 'page_terms', batch_size)
            if not batch:
                break
            process_page_terms_batch(conn, batch_size)
            logging.info(f"Processed terms for {len(batch)} pages")
            
    elif step_name == 'aggregate_terms':
        # Aggregate page terms to document terms
        while True:
            count = aggregate_document_terms(conn, batch_size // 2)
            if count == 0:
                break
            logging.info(f"Aggregated terms for {count} documents")
            
    elif step_name == 'reduce':
        # Only run when all documents have terms
        if all_documents_have_terms(conn):
            reduce_dimensionality_umap(conn)
        else:
            logging.warning("Skipping reduce - not all documents have terms")

def all_documents_have_terms(conn) -> bool:
    """Check if all documents have been processed for terms."""
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) 
        FROM documents d
        WHERE d.logically_deleted = false
        AND NOT EXISTS (
            SELECT 1 FROM document_terms dt
            WHERE dt.document_id = d.id
        )
    """)
    pending = cur.fetchone()[0]
    return pending == 0

def enqueue_initial_pipeline(conn):
    """Set up the entire pipeline queue."""
    
    # 1. Split documents
    enqueue_splits(conn)
    
    # 2. Extract text (handled by split step)
    
    # 3. Page terms - as soon as text is extracted
    enqueue_page_terms(conn)
    
    # 4. Embeddings (can run in parallel with page_terms)
    enqueue_embeddings(conn)
    
    # 5. Document embedding aggregation
    enqueue_document_embedding(conn)
    
    # 6. Aggregate terms
    enqueue_aggregate_terms(conn)
```

### 7. **Full Pipeline with Dependencies**

```python
def run_full_pipeline(conn, batch_size: int = 100):
    """Run the complete pipeline with proper step ordering."""
    
    # Define pipeline steps with dependencies
    pipeline_steps = [
        {
            'name': 'split',
            'enqueue': enqueue_splits,
            'process': lambda: process_split_batch(conn, batch_size),
            'depends_on': None
        },
        {
            'name': 'extract_text',
            'enqueue': None,  # Handled by split
            'process': None,
            'depends_on': 'split'
        },
        {
            'name': 'page_terms',
            'enqueue': enqueue_page_terms,
            'process': lambda: process_page_terms_batch(conn, batch_size),
            'depends_on': 'extract_text'
        },
        {
            'name': 'embed_pages',
            'enqueue': enqueue_embeddings,
            'process': lambda: embed_page_batch(conn, batch_size * 10),
            'depends_on': 'extract_text'
        },
        {
            'name': 'aggregate_terms',
            'enqueue': enqueue_aggregate_terms,
            'process': lambda: aggregate_document_terms(conn, batch_size // 2),
            'depends_on': 'page_terms'
        },
        {
            'name': 'reduce_dims',
            'enqueue': None,
            'process': lambda: reduce_dimensionality_umap(conn) if all_documents_have_terms(conn) else None,
            'depends_on': 'aggregate_terms'
        },
        {
            'name': 'cluster',
            'process': lambda: cluster_points(conn),
            'depends_on': 'reduce_dims'
        },
        {
            'name': 'label',
            'process': lambda: label_categories(conn),
            'depends_on': 'cluster'
        },
        {
            'name': 'colors',
            'process': lambda: assign_colors_to_categories(conn),
            'depends_on': 'label'
        },
        {
            'name': 'sizes',
            'process': lambda: update_document_sizes(conn),
            'depends_on': 'colors'
        }
    ]
    
    # Execute steps in order
    for step in pipeline_steps:
        if step['enqueue']:
            step['enqueue'](conn)
        
        if step['process']:
            # Check if dependencies are met
            if step['depends_on']:
                if not is_step_complete(conn, step['depends_on']):
                    logging.info(f"Waiting for {step['depends_on']} before {step['name']}")
                    continue
            
            logging.info(f"Starting step: {step['name']}")
            step['process']()
            logging.info(f"Completed step: {step['name']}")

def is_step_complete(conn, step_name: str) -> bool:
    """Check if a pipeline step is complete."""
    cur = conn.cursor()
    
    if step_name == 'split':
        cur.execute("""
            SELECT COUNT(*) FROM documents d
            WHERE NOT EXISTS (SELECT 1 FROM pages p WHERE p.document_id = d.id)
            AND d.logically_deleted = false
        """)
        return cur.fetchone()[0] == 0
    
    elif step_name == 'extract_text':
        cur.execute("""
            SELECT COUNT(*) FROM pages WHERE extracted_text IS NULL
        """)
        return cur.fetchone()[0] == 0
    
    elif step_name == 'page_terms':
        cur.execute("""
            SELECT COUNT(*) FROM pages p
            WHERE p.extracted_text IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM page_terms pt WHERE pt.page_id = p.id)
        """)
        return cur.fetchone()[0] == 0
    
    # ... etc for other steps
    
    return True
```

### 8. **Performance Comparison**

| Approach | Memory Usage | Speed | Resume-ability |
|----------|-------------|-------|----------------|
| **Original** (all docs) | High (loads all) | Fast once loaded | Poor (restart from scratch) |
| **Page-level** (new) | Low (batch per page) | Moderate | Excellent (per page) |
| **SQL Aggregation** (new) | Very Low | Fast (SQL optimized) | Excellent (per doc) |

### 9. **Monitoring Queries**

```sql
-- Check progress at each stage
SELECT 
    'documents' as stage,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE logically_deleted = false) as active,
    COUNT(*) FILTER (WHERE id IN (SELECT DISTINCT document_id FROM pages)) as has_pages,
    COUNT(*) FILTER (WHERE id IN (SELECT DISTINCT document_id FROM document_terms)) as has_terms
FROM documents
UNION ALL
SELECT 
    'pages' as stage,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE extracted_text IS NOT NULL) as has_text,
    COUNT(*) FILTER (WHERE id IN (SELECT DISTINCT page_id FROM page_terms)) as has_terms,
    COUNT(*) FILTER (WHERE embedding IS NOT NULL) as has_embedding
FROM pages;

-- Find documents stuck in processing
SELECT d.id, d.filename, 
    EXISTS(SELECT 1 FROM page_terms pt JOIN pages p ON p.id = pt.page_id WHERE p.document_id = d.id) as has_page_terms,
    EXISTS(SELECT 1 FROM document_terms dt WHERE dt.document_id = d.id) as has_doc_terms,
    EXISTS(SELECT 1 FROM pages p WHERE p.document_id = d.id AND p.embedding IS NULL) as missing_embeddings
FROM documents d
WHERE d.logically_deleted = false
ORDER BY d.id;
```

## Summary: Why This Works Better

### ✅ **Memory Efficient**
- Process one page at a time for terms
- Batch sizes control memory usage
- SQL handles aggregation (set-based, efficient)

### ✅ **Early Processing**
- Terms available as soon as text is extracted
- Can run parallel with embedding generation
- No need to re-process all documents later

### ✅ **Resume-able**
- Each page is processed independently
- If a page fails, it can be retried
- SQL aggregation can be rerun safely

### ✅ **SQL Does the Heavy Lifting**
- Aggregation is set-based (fast)
- No Python loops for merging
- Uses PostgreSQL's optimization

### ✅ **Works on Raspberry Pi**
- Low memory footprint
- Can be paused/resumed
- Clear progress tracking

The key insight is: **process terms at the page level, store them in `page_terms`, then use SQL to aggregate to `document_terms`**. This gives you the best of both worlds - immediate processing with low memory, and fast aggregation with SQL.

Would you like me to help you implement the SQL aggregation functions or design the monitoring queries?