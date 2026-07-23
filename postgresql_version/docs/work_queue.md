That's essentially a **work queue** pattern and it's a very solid idea. It's well-trodden in production data pipelines for exactly the reasons you're hitting — resumability, visibility, and decoupling the "what needs doing" from the "doing it."

---

## Simple version — one queue table

```sql
CREATE TABLE pipeline_queue (
    id              SERIAL PRIMARY KEY,
    document_id     INTEGER NOT NULL REFERENCES documents(id),
    page_id         INTEGER REFERENCES pages(id),
    step            TEXT NOT NULL,         -- 'split', 'extract_text', 'embed', 'reduce', 'cluster'
    status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'processing', 'done', 'error'
    error_msg       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (page_id, step)                 -- idempotent: can't enqueue same page+step twice
);

CREATE INDEX idx_queue_pending ON pipeline_queue (step, status) 
WHERE status = 'pending';
```

Then your worker just does:
```python
def claim_batch(conn, step: str, batch_size: int = 100):
    """Atomically claim a batch of work items."""
    cur = conn.cursor()
    cur.execute("""
        UPDATE pipeline_queue
        SET status = 'processing', updated_at = NOW()
        WHERE id IN (
            SELECT id FROM pipeline_queue
            WHERE step = %s AND status = 'pending'
            ORDER BY id
            LIMIT %s
            FOR UPDATE SKIP LOCKED          -- critical: safe for multiple workers
        )
        RETURNING page_id
    """, (step, batch_size))
    rows = cur.fetchall()
    conn.commit()
    return [r[0] for r in rows]

def mark_done(conn, page_id: int, step: str):
    cur = conn.cursor()
    cur.execute("""
        UPDATE pipeline_queue 
        SET status = 'done', updated_at = NOW()
        WHERE page_id = %s AND step = %s
    """, (page_id, step))
    conn.commit()

def mark_error(conn, page_id: int, step: str, error: str):
    cur = conn.cursor()
    cur.execute("""
        UPDATE pipeline_queue
        SET status = 'error', error_msg = %s, updated_at = NOW()
        WHERE page_id = %s AND step = %s
    """, (error, page_id, step))
    conn.commit()
```

---

## Populating the queue — one function per transition

```python
def enqueue_splits(conn):
    """Documents with no pages yet need splitting."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pipeline_queue (document_id, step)
        SELECT d.id, 'split'
        FROM documents d
        WHERE NOT EXISTS (SELECT 1 FROM pages p WHERE p.document_id = d.id)
        ON CONFLICT (page_id, step) DO NOTHING   -- idempotent
    """)
    conn.commit()

def enqueue_extractions(conn):
    """Pages with no extracted text yet."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pipeline_queue (document_id, page_id, step)
        SELECT p.document_id, p.id, 'extract_text'
        FROM pages p
        WHERE p.extracted_text IS NULL
        ON CONFLICT (page_id, step) DO NOTHING
    """)
    conn.commit()

def enqueue_embeddings(conn):
    """Pages with text but no embedding."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pipeline_queue (document_id, page_id, step)
        SELECT p.document_id, p.id, 'embed'
        FROM pages p
        WHERE p.extracted_text IS NOT NULL
        AND p.embedding IS NULL
        ON CONFLICT (page_id, step) DO NOTHING
    """)
    conn.commit()
```

---

## Visibility you get for free

```sql
-- progress dashboard
SELECT 
    step,
    status,
    COUNT(*) as count
FROM pipeline_queue
GROUP BY step, status
ORDER BY step, status;
```

```
step            status      count
extract_text    done        8432
extract_text    pending     1203
extract_text    error       12
embed           pending     8432
split           done        94
```

You immediately see where things are, what's errored, and what's backed up — without any custom instrumentation.

---

## The `FOR UPDATE SKIP LOCKED` part is important

That one clause is what makes it safe to run multiple workers simultaneously without double-processing. It's Postgres's built-in advisory row locking — any row being processed by worker A is invisible to worker B's claim query. You can open as many parallel worker windows as you want.

---

## Honest tradeoff

The queue table will itself accumulate bloat as rows move through `pending → done`. A simple fix is to periodically archive or delete done rows:

```sql
DELETE FROM pipeline_queue WHERE status = 'done';
```

Or keep them for audit history and just rely on the partial index on `WHERE status = 'pending'` to keep queries fast regardless.

Overall this is a much cleaner architecture than what you have now — your pipeline steps become stateless workers that just claim → process → mark done, and all the "where am I up to" logic lives in the queue.


Yes, exactly. Your current approach has a few compounding problems that the queue pattern resolves cleanly.

---

## Why NULL-based state tracking breaks down at scale

Your current queries all have this shape:
```sql
WHERE content IS NOT NULL AND extracted_text IS NULL
```

This is negative space logic — you're inferring state from the *absence* of data. It has several compounding problems:

**NULLs aren't indexed efficiently.** By default Postgres excludes NULLs from standard B-tree indexes, so `WHERE extracted_text IS NULL` can't use a normal index and often forces a seq scan across the entire table including all that TOAST blob data and dead tuples.

**State is implicit and fragile.** You have no way to distinguish between "hasn't been processed yet" and "was processed but failed" and "is currently being processed." They all look the same — NULL. So when something errors out, it silently re-enters the work pool on the next run with no record of what happened.

**The data and the workflow are entangled.** Your pipeline's progress is encoded inside your actual data table, which is why vacuuming, bloat, and query performance are all coupled together. Updating a row's state means touching the same physical page as the blob data.

---

## What the queue gives you computationally

The queue separates two concerns that should never have been together:

```
documents / pages tables  →  what the data IS
pipeline_queue            →  what needs to happen to it
```

The queue rows are tiny — just integers and short strings. They index perfectly, claim atomically with `SKIP LOCKED`, and their lifecycle (pending → done → deleted) is completely independent of your blob data. Vacuuming the queue is trivial because the rows are small and churn predictably.

---

## The larger principle — negative space is expensive

This shows up beyond just your SQL in a few ways worth naming:

**In storage** — you found this already. NULLing a column doesn't reclaim space, it creates dead tuples. The "empty" space isn't free, it's a liability that has to be managed.

**In query planning** — the planner has to reason about what *isn't* there. `WHERE x IS NULL` gives the planner much weaker statistical hooks than `WHERE status = 'pending'`. A queue with an explicit status column and a partial index is essentially pre-computing the answer to "what needs doing" so the planner never has to infer it.

**In your mental model** — NULL means too many things. It can mean "not yet processed," "intentionally empty," "failed silently," or "not applicable." When one value carries that much ambiguity, every query that touches it has to account for all those cases, and bugs hide in the gaps between them.

**In concurrency** — two workers looking for `WHERE extracted_text IS NULL` will both find the same rows and race. There's no safe way to claim work without a separate mechanism. The queue's `FOR UPDATE SKIP LOCKED` solves this at the database level with no application logic needed.

---

## The shift in thinking

What you're moving toward is essentially the difference between:

- **State as absence** — inferred from what's missing, scattered across your data tables, expensive to query, invisible when things go wrong
- **State as presence** — explicit rows in a dedicated table, cheap to query, observable, auditable, safe for parallel workers

The queue pattern is just one instance of a broader principle: **make your pipeline's state a first-class citizen in your schema** rather than something you read between the lines of your data. Once you do that, indexing, concurrency, resumability, and visibility all get easier simultaneously — because they're all downstream of the same root cause.