## Code Review: LRU Cache Implementation

Looking at your commit, solid first step toward caching! Here's my detailed review:

### ✅ **What Works Well**

1. **Clean separation** of cached `_raw_*` functions from public APIs
2. **Versioned invalidation** via `DATA_VERSION` - perfect for batch job scenarios
3. **Reasonable cache sizes** (`maxsize=128`, `64`) for your data volume
4. **Public API unchanged** - zero route refactoring needed

### ⚠️ **Critical Issues (Fix These First)**

#### 1. **SQL Injection Still Present**
```python
# BAD - still vulnerable!
sql = f"select id, file_path... where d.id = {id}"
```
Your commit fixed some but `/document/<id>` route still uses f-string interpolation. 
**Fix:**
```python
sql = "SELECT ... FROM documents WHERE id = %s"
cur.execute(sql, (doc_id,))
```

#### 2. **Gunicorn/Worker Pitfall** `[web:25]`
`lru_cache` is **process-local**. With multiple Gunicorn workers:
```
Worker 1: caches doc_id=123 → HIT
Worker 2: new process → MISS (recomputes)
```
**Solutions:**
```python
# Option A: Flask-Caching (Redis-backed, worker-shared)
from flask_caching import Cache
cache = Cache(app)

@cache.memoize(timeout=300)
def get_document_preview(doc_id: int):
    ...

# Option B: Clear on startup (for dev)
@lru_cache(maxsize=128)
def _raw_document_preview(doc_id: int, version: int):
    ...

def get_document_preview(doc_id: int):
    return _raw_document_preview(doc_id, DATA_VERSION)
```

#### 3. **Connection Leak Risk**
```python
# Multiple places still do:
con = get_db_connection()
curr = con.cursor()
# ... work ...
curr.close()  # Good
con.close()   # Good - but what if exception?
```
**Better:**
```python
def get_db_connection():
    return psycopg2.connect(...)  # Add pool later

# Use context manager
with get_db_connection() as conn:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
```

### 🔧 **Immediate Improvements**

#### 1. **Repository Pattern** (5 min win)
```python
class DataRepo:
    def count_docs(self) -> int:
        return get_count_docs()
    
    def topic_docs(self, topic_id: int | None = None):
        return get_topics_and_associated_documents(topic_id)
    
    def doc_preview(self, doc_id: int):
        return get_document_preview(doc_id)
    
    def doc_coords(self):
        return get_document_coords()

repo = DataRepo()  # Global singleton
```

Routes become trivial:
```python
@app.route("/")
def hello_world():
    return render_template('index.html', 
        count=repo.count_docs(),
        topic_data=repo.topic_docs(),
        x=repo.doc_coords(),
        date=datetime.datetime.now()
    )
```

#### 2. **Type Hints** (already partially there - finish it)
```python
@lru_cache(maxsize=128)
def _raw_topics_docs(topic_id: int | None, version: int) -> list[dict]:
    ...

def get_topics_and_associated_documents(topic_id: int | None = None) -> list[dict]:
    return _raw_topics_docs(topic_id, DATA_VERSION)
```

#### 3. **Cache Monitoring Endpoint**
```python
@app.route("/cache-stats")
def cache_stats():
    return {
        'count_docs': _raw_count_docs.cache_info(),
        'topics_docs': _raw_topics_docs.cache_info(),
        'doc_preview': _raw_document_preview.cache_info(),
        'data_version': DATA_VERSION
    }
```

### 🎯 **Production Readiness Checklist**

| Issue | Status | Priority |
|-------|--------|----------|
| SQL injection | ❌ Still in `/document` | **CRITICAL** |
| Gunicorn cache duplication | ❌ Process-local | **HIGH** |
| Connection management | ⚠️ Manual close() | MEDIUM |
| Cache invalidation | ✅ Versioned | DONE |
| Type hints | ⚠️ Partial | LOW |
| Monitoring | ❌ Missing | MEDIUM |

### 🚀 **Next Commit Priority Order**

```bash
1. Fix SQL injection in ALL routes (2 min)
2. Add context managers for DB connections (5 min)  
3. Add repo pattern + type hints (10 min)
4. Add /cache-stats endpoint (3 min)
5. Swap LRU → Flask-Caching for prod (15 min)
```

### **Future SQLAlchemy Path** ✅

Your structure is **perfect** for incremental SQLAlchemy:
```python
# Just rewrite internals, keep signatures
@lru_cache(maxsize=128)  # or @cache.memoize()
def _raw_count_docs(version: int) -> int:
    return db.session.query(func.count(Document.id)).scalar()
```

**Verdict:** Great foundation! Fix SQL injection first, then Gunicorn awareness. You're 90% of the way to production-ready data layer. The repo pattern makes this eminently testable and SQLAlchemy-migable.

Want me to write the SQL injection fixes or the full repo implementation?