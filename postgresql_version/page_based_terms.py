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
            CREATE TABLE if not exists page_terms (
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                term_id INTEGER NOT NULL REFERENCES terms(id),
                count INTEGER NOT NULL,  -- term frequency on this page
                PRIMARY KEY (page_id, term_id)
            );
        """)
    cur.execute("""
            CREATE INDEX if not exists idx_page_terms_page_id ON page_terms(page_id);
        """)    
    cur.execute("""
            CREATE INDEX if not exists idx_page_terms_term_id ON page_terms(term_id);    
        """)
    conn.commit()
    cur.close()
    print(f"✅ Database ready")
    return conn


# why not query for (min,max) for both doc and page ids so that 
# I blindly call this for x in min,max: for y in min,max ? after thinking on this it seems that many combos of ( doc_id, page_id ) will not be valid so 
# now I wonder why is the document_id even a part of this? wasn't the idea that f( text ) --> terms ? why does it matter what doc? 
#  clearly that can be looked up later for the aggregation but also it can be done in sql. really push back on why the inclusion of document_id, since if only page_id then a range w/ soft failures 
# can specify this 'work'

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
    
def get_minmax_page_ids( conn ):
    cur = conn.cursor()
    
    # Get page text
    cur.execute("SELECT min(id), max(id) from pages ")
    result = cur.fetchone()
    if not result or not result[0]:
        (0,0)
    cur.close()
    
    return (result[0], result[1])


# this works but it is very slow
if __name__ == '__main__':
    
    conn = init_db()
    min_page_id, max_page_id = get_minmax_page_ids( conn )
    print( f"attempting to process ids in the range {min_page_id} to {max_page_id}")
    [ process_page_terms( i, conn ) for i in range( min_page_id , max_page_id + 1) ]
    conn.close()