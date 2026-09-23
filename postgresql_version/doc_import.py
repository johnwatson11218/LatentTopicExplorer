import psycopg2
from psycopg2.extensions import connection as PGConnection

from psycopg2.extras import execute_values
import os
from pypdf import PdfReader

import re

# from nltk.stem import PorterStemmer
# from nltk.corpus import stopwords
# from collections import defaultdict
# from functools import lru_cache
# from sentence_transformers import SentenceTransformer

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
    
    # this only needs to be done once per installation of postgresql after the pg-vector extension
    # is installed. I installed the extension at the operating system level w/ sudo apt install postgresql-16-pgvector(sp?)
    cur.execute( "CREATE EXTENSION IF NOT EXISTS vector")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id          SERIAL PRIMARY KEY,
            filename    TEXT NOT NULL UNIQUE,            
            file_size   INTEGER,
            inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            embedding   vector(384)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id             SERIAL PRIMARY KEY,
            document_id    INTEGER REFERENCES documents(id),
            content bytea,
            extracted_text TEXT,
            page_number    INTEGER NOT NULL,
            inserted_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            embedding      vector(384)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS terms (
            id   SERIAL PRIMARY KEY,
            term TEXT NOT NULL UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_terms (
            id          SERIAL PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES documents(id),
            term_id     INTEGER NOT NULL REFERENCES terms(id),
            tf          REAL,       -- term frequency (count / total_terms_in_doc)
            raw_count   INTEGER,    -- how many times the term appears in this doc
            page_count  INTEGER,    -- how many pages it appears on
            UNIQUE (document_id, term_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_coordinates (
            document_id INTEGER NOT NULL REFERENCES documents(id),
            x           REAL,
            y           REAL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id    SERIAL PRIMARY KEY,
            label TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_categories (
            document_id INTEGER NOT NULL REFERENCES documents(id),
            category_id INTEGER REFERENCES categories(id)
        )
    """)


    cur.execute( """
                
                CREATE TABLE if not exists pipeline_queue (
                        id              SERIAL PRIMARY KEY,
                        document_id     INTEGER NOT NULL REFERENCES documents(id),
                        page_id         INTEGER REFERENCES pages(id),
                        step            TEXT NOT NULL,         -- 'split', 'extract_text', 'embed', 'reduce', 'cluster'
                        status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'processing', 'done', 'error'
                        error_msg       TEXT,
                        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (page_id, step)                 -- idempotent: can't enqueue same page+step twice
                    )
                """)
    
    cur.execute( """
                    CREATE INDEX if not exists idx_queue_pending ON pipeline_queue (step, status) 
                    WHERE status = 'pending'
                """)
    
    conn.commit()
    cur.close()
    print(f"✅ Database ready")
    return conn

# TODO - make this function idempotent by looking in db first so that there is no double entry.
def scan_folder(  conn = None, file_path : str = "data" ) -> None:
    print( f"starting scan file_path ={file_path}, conn {conn}")
    for root, dirs, files in os.walk(file_path):                
        cur = conn.cursor()
        for filename in files:
            if filename.endswith(".pdf") and not filename.startswith("."):
                try:
                    path = os.path.join(root, filename)
                    cur.execute("insert into documents ( filename, file_size ) values ( %s,%s )  on conflict (filename) do nothing", (path, 0,  ) )
                    conn.commit()
                except Exception as e:
                    print( f"Got an error {e}")
                    conn.rollback()
        cur.close()

def clean_text_for_postgres(text):
    if not text: return ""
    text = text.replace('\x00', '')
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    return text.strip()

def clip_to_byte_limit(s, byte_limit=1048575):
    s_bytes = s.encode('utf-8')
    if len(s_bytes) <= byte_limit:
        return s
    return s_bytes[:byte_limit].decode('utf-8', errors='ignore')

if __name__ == "__main__":
    print( "running" )
    init_db()
    conn = get_db_connection()
    scan_folder( conn, "/mnt/usbstick/doc_import/" ) 
    conn.close()
