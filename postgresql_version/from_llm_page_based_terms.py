import psycopg2
from psycopg2.extensions import connection as PGConnection
from pgvector.psycopg2 import register_vector
from psycopg2.extras import execute_values
import os
from pypdf import PdfReader
import io
import re
import nltk
from nltk.stem import PorterStemmer
from nltk.corpus import stopwords
from collections import Counter, defaultdict
from functools import lru_cache
from sentence_transformers import SentenceTransformer
import numpy as np
import umap
import hdbscan

def get_db_connection( 
    host: str = 'rp',
    port: int = 5432,
    dbname: str = "second_brain",
    user: str = "postgres",
    password: str = "test_case",
                   
) -> PGConnection:
    return  psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
    )

def init_db(
) -> PGConnection:
    """Connect to a PostgreSQL database and set up tables."""
    conn = get_db_connection()
    cur = conn.cursor()
        
    cur.execute("""
            CREATE TABLE if not exists page_terms_llm (
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                term_id INTEGER NOT NULL REFERENCES terms(id),
                count INTEGER NOT NULL,  -- term frequency on this page
                PRIMARY KEY (page_id, term_id)
            );
        """)
    cur.execute("""
            CREATE INDEX if not exists idx_page_terms_page ON page_terms_llm(page_id);
        """)    
    cur.execute("""
            CREATE INDEX if not exists idx_page_terms_term ON page_terms_llm(term_id);    
        """)
    conn.commit()
    cur.close()
    print(f"✅ Database ready")
    return conn


stemmer = PorterStemmer()
STOP_WORDS = set(stopwords.words('english'))

@lru_cache(maxsize=None)
def stem_cached(token: str) -> str:
    # same input always stems the same way, so cache it - avoids
    # re-stemming the same common words over and over across 420k pages
    return stemmer.stem(token)

def clean_and_tokenize(text: str) -> list[str]:
    tokens = re.findall(r'\b[a-z]{2,}\b', text.lower())
    return [stem_cached(t) for t in tokens if t not in STOP_WORDS]


def process_page_terms(page_id: int, conn):
    """
    Process terms for a single page immediately after text extraction.
    Updates page_terms table (new) and document_terms (aggregated later).
    Kept for single-page/debug use; process_page_chunk is the fast path.
    """
    process_page_chunk([page_id], conn)


def process_page_chunk(page_ids: list[int], conn):
    """
    Batched, idempotent version of process_page_terms for a whole chunk of
    page_ids at once. Cuts round trips from O(unique_terms * pages) down to
    a handful of statements per chunk:

      1) fetch all page texts in one query
      2) bulk-insert every distinct new term in the chunk (ON CONFLICT DO NOTHING)
      3) bulk-fetch term -> id for every term in the chunk
      4) bulk-upsert all (page_id, term_id, count) rows

    Safe to call repeatedly (or concurrently, from other processes) for the
    same page_ids: terms.term is UNIQUE so concurrent inserts of a brand-new
    term collapse to one row, and page_terms is upserted by primary key.
    """
    if not page_ids:
        return

    cur = conn.cursor()

    cur.execute(
        "SELECT id, extracted_text FROM pages WHERE id = ANY(%s)",
        (page_ids,)
    )
    rows = cur.fetchall()

    page_term_counts: dict[int, Counter] = {}
    all_terms: set[str] = set()
    for page_id, text in rows:
        if not text:
            continue
        tokens = clean_and_tokenize(text)
        if not tokens:
            continue
        counts = Counter(tokens)
        page_term_counts[page_id] = counts
        all_terms.update(counts.keys())

    if not all_terms:
        conn.commit()
        return

    term_list = list(all_terms)

    # Bulk-create any terms that don't exist yet.
    execute_values(
        cur,
        "INSERT INTO terms (term) VALUES %s ON CONFLICT (term) DO NOTHING",
        [(t,) for t in term_list]
    )

    # Bulk-resolve every term (old and new) to its id in one round trip.
    cur.execute(
        "SELECT term, id FROM terms WHERE term = ANY(%s)",
        (term_list,)
    )
    term_to_id = dict(cur.fetchall())

    page_term_rows = [
        (page_id, term_to_id[term], count)
        for page_id, counts in page_term_counts.items()
        for term, count in counts.items()
    ]

    execute_values(
        cur,
        """
        INSERT INTO page_terms_llm (page_id, term_id, count)
        VALUES %s
        ON CONFLICT (page_id, term_id) DO UPDATE SET
            count = EXCLUDED.count
        """,
        page_term_rows
    )

    conn.commit()
    cur.close()


def _process_chunk_worker(page_ids: list[int]):
    """
    Entry point for a worker process: opens its own connection (psycopg2
    connections can't be shared/pickled across processes), does the chunk,
    then closes. Any exception is caught and returned so one bad chunk
    doesn't kill the whole pool silently.
    """
    conn = get_db_connection()
    try:
        process_page_chunk(page_ids, conn)
        return (page_ids[0], page_ids[-1], None)
    except Exception as e:
        conn.rollback()
        return (page_ids[0], page_ids[-1], str(e))
    finally:
        conn.close()


def chunk_ranges(min_id: int, max_id: int, chunk_size: int = 200):
    """Yield lists of contiguous page_ids of size chunk_size (inclusive of max_id)."""
    current = min_id
    while current <= max_id:
        end = min(current + chunk_size - 1, max_id)
        yield list(range(current, end + 1))
        current = end + 1


def get_minmax_page_ids( conn ):
    cur = conn.cursor()
    
    # Get page text
    cur.execute("SELECT min(id), max(id) from pages ")
    result = cur.fetchone()
    if not result or not result[0]:
        (0,0)
    cur.close()
    
    return (result[0], result[1])


if __name__ == '__main__':
    import time
    from concurrent.futures import ProcessPoolExecutor, as_completed

    CHUNK_SIZE = 200      # pages per unit of work
    MAX_WORKERS = 4       # keep modest on a Pi-hosted server; it has to serve all connections too

    conn = init_db()
    min_page_id, max_page_id = get_minmax_page_ids(conn)
    conn.close()  # main process doesn't need to hold a connection during the pool run

    print(f"attempting to process ids in the range {min_page_id} to {max_page_id}")

    chunks = list(chunk_ranges(min_page_id, max_page_id, CHUNK_SIZE))
    start = time.time()
    errors = []

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_process_chunk_worker, c) for c in chunks]
        done = 0
        for fut in as_completed(futures):
            lo, hi, err = fut.result()
            done += 1
            if err:
                errors.append((lo, hi, err))
                print(f"  ✗ chunk {lo}-{hi} failed: {err}")
            if done % 10 == 0 or done == len(chunks):
                print(f"  {done}/{len(chunks)} chunks done ({time.time() - start:.1f}s elapsed)")

    print(f"✅ done in {time.time() - start:.1f}s, {len(errors)} chunk(s) failed")
    if errors:
        print("Re-run the script to retry — processing is idempotent, so failed/incomplete "
              "chunks will simply be recomputed to the same correct result.")