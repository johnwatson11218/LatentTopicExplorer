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
from collections import defaultdict
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

def scan_folder(  conn = None, file_path : str = "data" ) -> None:
    print( f"starting scan file_path ={file_path}, conn {conn}")
    for root, dirs, files in os.walk(file_path):        
        #print( f"root = {root} len(dirs ) {len(dirs)} len(files) {len(files)}")
        cur = conn.cursor()
        for filename in files:
            
            
            if filename.endswith(".pdf") and not filename.startswith("."):
                try:
                    
                    path = os.path.join(root, filename)
                    # with open(path, "rb") as f:
                    #     pdf_bytes = f.read()

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

def split_pdf_file_and_extract_text( document_id, conn  ):
    
    try:
        print( f"processing document {document_id}")
        cur = conn.cursor()
        sql = "select filename from documents where id = %s "
        cur.execute( sql , (document_id,) )
        path = cur.fetchone()[0]
    except Exception as e :
        mark_error( conn, document_id , 'split' )
        return 

    with open(path, "rb") as f:
        blob = f.read()
        print( f"The blob is length = {len( blob )}")
        total_pages = 0
        try:
            reader = PdfReader( io.BytesIO( blob ))
            total_pages = len( reader.pages )
        except Exception as e:
            mark_error( conn, document_id , 'split' )
            return
        raw_text  = "ERROR PARSING PAGE"
        for page_num in range(total_pages):           
            page = reader.pages[page_num]
            try:
                raw_text = page.extract_text(extraction_mode='layout')
                raw_text = clean_text_for_postgres( raw_text )
                raw_text = clip_to_byte_limit( raw_text )
                
                page_sql = "insert into pages ( document_id , extracted_text , page_number ) values ( %s,%s,%s )"
                cur.execute(page_sql , (document_id , raw_text, page_num ))
            except Exception as e :
                mark_error( conn, document_id , 'split' )
                return
            conn.commit()
    mark_done(conn, document_id, 'split')

    cur.close()

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

def populate_terms_old(conn=None):
    print('starting populate_terms()')
    nltk.download('stopwords', quiet=True)
    nltk.download('punkt', quiet=True)

    STOP_WORDS = set(stopwords.words('english'))
    stemmer = PorterStemmer()

    def clean_and_tokenize(text: str) -> list[str]:
        tokens = re.findall(r'\b[a-z]{2,}\b', text.lower())
        return [stemmer.stem(t) for t in tokens if t not in STOP_WORDS]

    cur = conn.cursor()
    cur.execute("""
        SELECT d.id FROM documents d
        WHERE NOT EXISTS (
            SELECT 1 FROM document_terms dt WHERE dt.document_id = d.id
        )
    """)
    document_ids = cur.fetchall()
    print(f"There are {len(document_ids)} documents to process.")

    for (document_id,) in document_ids:
        print(f"processing document_id = {document_id}")
        cur.execute("""
            SELECT id, extracted_text FROM pages
            WHERE extracted_text IS NOT NULL
              AND document_id = %s
            ORDER BY page_number
        """, (document_id,))
        pages = cur.fetchall()

        # accumulate across ALL pages first
        term_stats: dict = defaultdict(lambda: {"count": 0, "pages": set()})
        for page_id, text in pages:
            for term in clean_and_tokenize(text):
                term_stats[term]['count'] += 1
                term_stats[term]['pages'].add(page_id)

        # then write to DB once
        total_tokens = sum(s['count'] for s in term_stats.values())
        doc_cursor = conn.cursor()
        for term, stats in term_stats.items():
            """
            doc_cursor.execute( 'INSERT INTO terms (term) VALUES (%s) ON CONFLICT (term) DO UPDATE SET term = EXCLUDED.term RETURNING id', (term,))
            """
            
            #doc_cursor.execute('INSERT INTO terms (term) VALUES (%s) ON CONFLICT(term) DO UPDATE SET term=term RETURNING id', (term,) )
            doc_cursor.execute(' insert into terms ( term ) values ( %s ) on conflict ( term ) do update set term = excluded.term returning id', ( term, ))
            term_id = doc_cursor.fetchone()[0]
            
            
            tf = stats['count'] / total_tokens if total_tokens else 0
            doc_cursor.execute("""
                INSERT INTO document_terms (document_id, term_id, raw_count, tf, page_count)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT(document_id, term_id) DO UPDATE SET
                    raw_count  = excluded.raw_count,
                    tf         = excluded.tf,
                    page_count = excluded.page_count
            """, (document_id, term_id, stats['count'], tf, len(stats['pages'])))

        conn.commit()
        doc_cursor.close()

    cur.close()
    print('end populate_terms()')

globalModel = None
def getLLModel():
    global globalModel
    if globalModel is None:
        globalModel = SentenceTransformer( 'all-MiniLM-L6-v2')
    return globalModel

def embed_single_page( page_id, conn ):
    register_vector( conn)
    cur = conn.cursor()
    cur.execute( "select extracted_text from pages where id = %s ", ( page_id, ))
    rows = cur.fetchall()
    if len( rows ) > 0:
        model = getLLModel()

    for  (text,) in rows:
        vec = model.encode( text, normalize_embeddings=True )
        #print( f"embed single page {page_id} text {text} of length {len(text)} vec is {vec.shape}" )
        cur.execute( "update pages set embedding = %s where id = %s ", ( vec.astype( np.float32), page_id ))
    mark_done(conn, page_id, 'embed')
    conn.commit()            
    """
    
    
    
                        update documents as d set embedding  = ps.embedding from (select document_id as document_id ,   avg( embedding  ) as embedding 
                        from pages p group by p.document_id) as ps( document_id, embedding ) where id = ps.document_id;  
                        
                        
                        alter table pages add column if not exists  page_size int generated always as (length( extracted_text )) stored;
                        
                        alter table documents add column if not exists size int default 0;
                        
                        update documents d set size = sub.x 
                        from ( select p.document_id, sum( length( p.extracted_text ) ) as x from pages p group by p.document_id ) sub
                        where d.id = sub.document_id 

    
    
                            
                            alter table document_categories add column if not exists color varchar;
                            
                                                
            -- need a way to manually remove docs from main collection. 
            -- logical delete, new column, default false etc. 
            
            
            alter table documents add column if not exists logically_deleted bool default false;
            
            
            
            
            
            #next ideas for python code session 
            
            import math

            sizes = plot_data['o_sizes']
            log_sizes = [math.log1p(s) for s in sizes]

            min_log, max_log = min(log_sizes), max(log_sizes)
            log_range = max_log - min_log or 1  # avoid divide-by-zero if all sizes are equal

            MIN_PX, MAX_PX = 4, 40  # floor so nothing is invisible, ceiling so nothing swamps the plot

            plot_data['sizes'] = [
                MIN_PX + (MAX_PX - MIN_PX) * (ls - min_log) / log_range
                for ls in log_sizes
            ]

    """
    cur.close()

def reduce_dimensionality_umap(conn):
    # load all the embeddings from the documents table
    cur = conn.cursor()
    register_vector(conn)      
    cur.execute( " select d.id, d.embedding from documents d where embedding is not null")
    rows = cur.fetchall()
    print( f"There are {len( rows )} documents to pass to umap ... ")
    
    ids = [r[0] for r in rows]
    vectors = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    print(f"matrix shape: {vectors.shape}")  # should be (33, 384)

    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=5,
        min_dist=0.1,
        metric='cosine'
    )
    embedding_2d = reducer.fit_transform(vectors)
    print(f"reduced shape: {embedding_2d.shape}")  
    cur.execute( "delete from document_coordinates")
    for id, data in zip( ids, embedding_2d ):
        cur.execute("insert into document_coordinates ( document_id, x, y ) values ( %s , %s , %s )" , ( id, float(data[0]), float(data[1] ), ))
    cur.close()
    conn.commit()

def cluster_points( conn ):
    cur = conn.cursor()
    
    cur.execute( "select document_id, x, y from document_coordinates " )
    rows = cur.fetchall()
    
    ids = [ r[0] for r in rows ]
    points = [ ( r[1],r[2]) for r in rows ]
    
    
    clusterer = hdbscan.HDBSCAN()
    clusterer.fit( points )
    
    cur.execute( "delete from document_categories" )
    cur.execute( "delete from categories" )
    
    for label_id in set(clusterer.labels_):
        print( f"{label_id} of type {type(label_id)}")
        cur.execute( "insert into categories ( id ) values ( %s ) ", (int(label_id),))
        
    for id, label in zip( ids,clusterer.labels_ ):
        cur.execute( " insert into document_categories ( document_id , category_id ) values (%s,%s)",(id,int(label),))
    conn.commit()
    cur.close()
    
def label_categories(conn):
    cur = conn.cursor()
    
    # for each category, get all documents in it
    cur.execute("SELECT DISTINCT category_id FROM document_categories WHERE category_id >= 0")
    category_ids = [r[0] for r in cur.fetchall()]
    
    for category_id in category_ids:
        # get all documents in this cluster
        cur.execute("""
            SELECT document_id FROM document_categories 
            WHERE category_id = %s
        """, (category_id,))
        doc_ids = [r[0] for r in cur.fetchall()]
        
        if not doc_ids:
            continue
        
        # sum TF-IDF across all docs in cluster
        # IDF = ln(total_docs / docs_containing_term)
        cur.execute("SELECT COUNT(*) FROM documents")
        total_docs = cur.fetchone()[0]
        
        #placeholders = ','.join('?' * len(doc_ids))
        cur.execute(f"""
            SELECT 
                dt.term_id,
                t.term,
                SUM(dt.raw_count) as total_freq,
                COUNT(DISTINCT dt.document_id) as doc_freq
            FROM document_terms dt
            JOIN terms t ON t.id = dt.term_id
            WHERE dt.document_id = any( %s )
            GROUP BY dt.term_id, t.term
        """, (doc_ids,) )
        
        rows = cur.fetchall()
        
        # compute TF-IDF per term for this cluster
        import math
        scored = []
        for term_id, term, total_freq, doc_freq in rows:
            idf = math.log(total_docs / max(doc_freq, 1))
            tfidf = total_freq * idf
            scored.append((term, tfidf))
        
        # top 5 terms become the label
        scored.sort(key=lambda x: x[1], reverse=True)
        top_terms = [t[0] for t in scored[:15]]
        label = '-'.join(top_terms)
        
        print(f"category {category_id}: {label}")
        cur.execute(
            "UPDATE categories SET label = %s WHERE id = %s",
            (label, category_id)
        )
    
    conn.commit()
    cur.close()

#####################################################################

def claim_batch(conn, step: str, batch_size: int = 5 ):
    """Atomically claim a batch of work items."""
    cur = conn.cursor()
    col = 'page_id'
    if step == 'split':
        col =  'document_id'        
    cur.execute(f"""
        UPDATE pipeline_queue
        SET status = 'processing', updated_at = NOW()
        WHERE id IN (
            SELECT id FROM pipeline_queue
            WHERE step = %s AND status = 'pending'
            ORDER BY id
            LIMIT %s
            FOR UPDATE SKIP LOCKED          -- critical: safe for multiple workers
        )
        RETURNING {col}
    """, (step, batch_size))
    rows = cur.fetchall()
    conn.commit()
    return [r[0] for r in rows]

def mark_done(conn, page_id: int, step: str):
    cur = conn.cursor()
    col = 'page_id'
    if step == 'split':
        col = 'document_id'
    cur.execute(f"""
        UPDATE pipeline_queue 
        SET status = 'done', updated_at = NOW()
        WHERE {col} = %s AND step = %s
    """, (page_id, step))
    conn.commit()

def mark_error(conn, id: int, step: str, error: str):
    cur = conn.cursor()
    col = 'page_id'
    if step == 'split':
        col = 'document_id'
    cur.execute(f" UPDATE pipeline_queue    SET status = 'error', error_msg = %s, updated_at = NOW()     WHERE {col} = %s AND step = %s ", (error, id, step))
    conn.commit()

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

#####################################################################

init_db()

# conn = get_db_connection()
# scan_folder( conn, "static/books/" ) 
# conn.close()

# for i in range( 5 ):
#     conn = get_db_connection()
#     #
#     enqueue_splits( conn )
    
#     enqueue_embeddings( conn )
#     # process each batch type in order
#     rows = claim_batch(conn, 'split', 10 )
#     [ split_pdf_file_and_extract_text( document_id, conn ) for document_id in rows  ]
    
#     rows = claim_batch( conn , 'embed', 5000 )
#     print( f"number for embed text {len( rows )}")
#     [ embed_single_page( page_id, conn ) for page_id in rows ]
    
#     conn.close()
    
print( f"done with pdf import steps ")
done_importing = True

if done_importing == True:
    conn = get_db_connection()
    print( "done importing, running corpus level steps. terms, reduce dimensionality, cluster, label etc. ")
    print( "calling populate_terms()")
    populate_terms( conn) 
    print( "calling reduce_dimensionality_umap()")
    reduce_dimensionality_umap( conn )
    print( "calling cluster_points()")
    cluster_points( conn )
    print( "calling label_categories()")
    label_categories( conn )
    conn.close()
    
print( f"Done with all steps. ")
# populate_terms( conn )
# populate_embeddings( conn )
# reduce_dimensionality_umap(conn)
# cluster_points( conn )
#label_categories( conn )
