import psycopg2
from psycopg2.extensions import connection as PGConnection
from pgvector.psycopg2 import register_vector
from psycopg2.extras import execute_values
import numpy as np
import re
from collections import defaultdict
import nltk
from nltk.stem import PorterStemmer
from nltk.corpus import stopwords
from functools import lru_cache

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

def populate_terms(conn=None):
    print('starting populate_terms()')
    nltk.download('stopwords', quiet=True)
    nltk.download('punkt', quiet=True)

    STOP_WORDS = set(stopwords.words('english'))
    stemmer = PorterStemmer()

    @lru_cache(maxsize=None)
    def stem_cached(token: str) -> str:
        # same input always stems the same way, so cache it - avoids
        # re-stemming the same common words over and over across 420k pages
        return stemmer.stem(token)

    def clean_and_tokenize(text: str) -> list[str]:
        tokens = re.findall(r'\b[a-z]{2,}\b', text.lower())
        return [stem_cached(t) for t in tokens if t not in STOP_WORDS]

    cur = conn.cursor()
    cur.execute("""
        SELECT d.id FROM documents d
        WHERE NOT EXISTS (
            SELECT 1 FROM document_terms dt WHERE dt.document_id = d.id
        )
    """)
    document_ids = [r[0] for r in cur.fetchall()]
    print(f"There are {len(document_ids)} documents to process.")

    # --- Pass 1: tokenize every doc locally, no DB writes yet ---
    # This is the part that used to do one INSERT...RETURNING per term per doc
    # (potentially 1M+ round trips to a remote host). Instead we build
    # everything in memory first, then hit the DB in a handful of batched calls.
    doc_term_stats = {}
    all_terms = set()

    for document_id in document_ids:
        cur.execute("""
            SELECT id, extracted_text FROM pages
            WHERE extracted_text IS NOT NULL
              AND document_id = %s
            ORDER BY page_number
        """, (document_id,))
        pages = cur.fetchall()

        term_stats: dict = defaultdict(lambda: {"count": 0, "pages": set()})
        for page_id, text in pages:
            for term in clean_and_tokenize(text):
                term_stats[term]['count'] += 1
                term_stats[term]['pages'].add(page_id)

        doc_term_stats[document_id] = term_stats
        all_terms.update(term_stats.keys())

    print(f"Tokenized {len(document_ids)} documents, found {len(all_terms)} unique terms.")

    # --- Pass 2: upsert every unique term ONCE, then pull the whole term->id map back in one query ---
    if all_terms:
        execute_values(
            cur,
            "INSERT INTO terms (term) VALUES %s ON CONFLICT (term) DO NOTHING",
            [(t,) for t in all_terms],
        )
        conn.commit()

    cur.execute("SELECT term, id FROM terms")
    term_id_map = dict(cur.fetchall())

    # --- Pass 3: bulk-insert document_terms rows, one round trip per document instead of one per term ---
    for document_id, term_stats in doc_term_stats.items():
        total_tokens = sum(s['count'] for s in term_stats.values())
        rows = [
            (
                document_id,
                term_id_map[term],
                stats['count'],
                stats['count'] / total_tokens if total_tokens else 0,
                len(stats['pages']),
            )
            for term, stats in term_stats.items()
        ]

        if rows:
            execute_values(
                cur,
                """
                INSERT INTO document_terms (document_id, term_id, raw_count, tf, page_count)
                VALUES %s
                ON CONFLICT (document_id, term_id) DO UPDATE SET
                    raw_count  = excluded.raw_count,
                    tf         = excluded.tf,
                    page_count = excluded.page_count
                """,
                rows,
            )
        conn.commit()

    cur.close()
    print('end populate_terms()')




if __name__ == "__main__":
    print( "running group operations, assumes that all the embeddings have been calculated for all the pages and documents." )
    
    conn = get_db_connection()    

    populate_terms( conn )
    # populate_embeddings( conn )
    # reduce_dimensionality_umap(conn)
    # cluster_points( conn )
    # label_categories( conn )
    conn.close()
