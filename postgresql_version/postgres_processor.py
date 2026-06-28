import psycopg2
from psycopg2.extensions import connection as PGConnection
from pgvector.psycopg2 import register_vector

import os
from pypdf import PdfReader, PdfWriter
import io
import re
import nltk
from nltk.stem import PorterStemmer
from nltk.corpus import stopwords

from collections import defaultdict

from sentence_transformers import SentenceTransformer
import numpy as np

import umap
import hdbscan



def get_db_connection( 
    host: str = "192.168.86.242",
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
            filename    TEXT NOT NULL,
            content     BYTEA,
            file_size   INTEGER,
            inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            embedding   vector(384)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id             SERIAL PRIMARY KEY,
            document_id    INTEGER REFERENCES documents(id),
            content        BYTEA,
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
                    with open(path, "rb") as f:
                        pdf_bytes = f.read()

                    cur.execute("insert into documents ( filename, content, file_size ) values ( %s,%s,%s )", (filename,pdf_bytes, len(pdf_bytes)) )
                    conn.commit()
                except Exception as e:
                    print( f"Got an error {e}")
                    conn.rollback()
        cur.close()

def split_pdf_files( conn = None ):
    
    cur = conn.cursor()
    cur.execute( " select d.id from documents d where not exists ( select 1 from pages p where p.document_id = d.id ) order by d.id limit 10")
    ids = cur.fetchall()
    print( f"There are {len( ids )} documents to split_pdf_files ... ")
    
    for row in ids:
        document_id = row[0]
        print( f"processing document {document_id}")
        sql = "select content from documents where id = %s "
        cur.execute( sql , (document_id,) )
        blob = cur.fetchone()[0]

        reader = PdfReader( io.BytesIO( blob ))
        for page_num in range(len(reader.pages)):
            writer = PdfWriter()
            writer.add_page(reader.pages[page_num])            
            output_buffer = io.BytesIO()
            writer.write( output_buffer )
            page_blob = output_buffer.getvalue()
            page_sql = "insert into pages ( document_id , content, page_number ) values ( %s,%s,%s )"
            cur.execute(page_sql , (document_id , page_blob, page_num ))
            output_buffer.close()
            conn.commit()
       
    cur.close()



def extract_text_from_stored_pages( conn = None ):
    cur = conn.cursor()
    cur.execute( "select id, content from pages where content is not null and extracted_text is null limit 1000 ") 
    rows = cur.fetchall()
    print(f"found {len(rows)} pages to attempt to extract text from")
    for row in rows:
        page_id, page_blob = row
        try:            
            reader = PdfReader( io.BytesIO( page_blob ))
            page = reader.pages[0]
            raw_text = page.extract_text(extraction_mode='layout')
            update_cur = conn.cursor()
            update_cur.execute( "update pages set extracted_text = %s where id = %s ", (raw_text, page_id))
            conn.commit()
            update_cur.close()
        except Exception as e:
            print(f"Exception {e}")
    cur.close()


def populate_terms(conn=None):
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
            doc_cursor.execute(
    'INSERT INTO terms (term) VALUES (%s) ON CONFLICT (term) DO UPDATE SET term = EXCLUDED.term RETURNING id',
    (term,)
)
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



def populate_embeddings(conn):
    register_vector(conn)
    cur = conn.cursor()
    
    # 1. embed each page
    cur.execute(""" SELECT id, extracted_text FROM pages WHERE extracted_text IS NOT NULL  AND embedding IS NULL limit 5000""")
    rows = cur.fetchall()
    if len( rows ) > 0 : 
        model = SentenceTransformer('all-MiniLM-L6-v2')    
        
    for page_id, text in rows:
        vec = model.encode(text, normalize_embeddings=True)
        cur.execute( "UPDATE pages SET embedding = %s WHERE id = %s", (vec.astype(np.float32), page_id)     )
    conn.commit()

    # # 2. average page embeddings up to document level
    # cur.execute(""" SELECT DISTINCT document_id FROM pages        WHERE embedding IS NOT NULL """)
    # for (doc_id,) in cur.fetchall():
    #     cur.execute(            "SELECT embedding FROM pages WHERE document_id = ? AND embedding IS NOT NULL",(doc_id,))
    #     vecs = np.stack([            np.frombuffer(row[0], dtype=np.float32)             for row in cur.fetchall()        ])
    #     doc_vec = vecs.mean(axis=0)
    #     cur.execute( "UPDATE documents SET embedding = %s WHERE id = %s",   (doc_vec.astype(np.float32), doc_id))
    # conn.commit()
    """
    update documents as d set embedding  = ps.embedding from (select document_id as document_id ,   avg( embedding  ) as embedding from pages p group by p.document_id) as ps( document_id, embedding ) where id = ps.document_id  
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



init_db()


conn = get_db_connection()

# scan_folder( conn, "data" )
# split_pdf_files( conn )
# extract_text_from_stored_pages( conn )
# populate_terms( conn )
# populate_embeddings( conn )
# reduce_dimensionality_umap(conn)
# cluster_points( conn )
label_categories( conn )

#     cluster_points( conn )
#     label_categories( conn )