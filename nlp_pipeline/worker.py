import os
import re
import json
import time
import redis
import psycopg2
import pdfplumber
from psycopg2.extras import execute_values
from sentence_transformers import SentenceTransformer
import numpy as np
from typing import List, Tuple, Dict, Set
from collections import defaultdict, Counter

from umap import UMAP

from sklearn.cluster import HDBSCAN
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd


import spacy
import re

from celery import group
from celery_worker import embed_single_document

# --- CONFIGURATION ---
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379')
DB_CONFIG = {
    "dbname": "second_brain",
    "user": "postgres",
    "password": "test_case",
    "host": "postgres",
    "port": 5432
}
MAX_BYTES = 1048575
DATA_FOLDER = "data/"



def get_documents():
    """Fetch all documents with id and raw_text from the documents table"""
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id, raw_text FROM documents d where d.embedding is null")
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

def chunk_text(text: str, chunk_size: int = 100, overlap: int = 10) -> List[str]:
    """
    Split text into overlapping chunks
    chunk_size: length of each chunk
    overlap: number of characters to overlap between chunks
    """
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    step = chunk_size - overlap
    
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        
        # If this chunk reaches the end, we're done
        if end >= len(text):
            break
            
        start += step
    
    return chunks

def insert_chunked_embeddings(document_id: int, chunks: List[str], embeddings: List[np.ndarray]):
    """Insert chunked embeddings into the database"""
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    try:
        for seq_num, (chunk, embedding) in enumerate(zip(chunks, embeddings), 1):
            # Convert numpy array to list for PostgreSQL
            embedding_list = embedding.tolist()
            
            cursor.execute("""
                INSERT INTO chunked_embeddings (input_text, embedding, sequence_number, document_id)
                VALUES (%s, %s, %s, %s)
            """, (chunk, embedding_list, seq_num, document_id))
        
        conn.commit()
        print(f"Inserted {len(chunks)} chunks for document {document_id}")
        
    except Exception as e:
        conn.rollback()
        print(f"Error inserting chunks for document {document_id}: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

def update_doc_embedding(document_id: int,  embedding: np.ndarray):
    """Insert chunked embeddings into the database"""
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    try:        
        cursor.execute("""
            update documents set embedding = %s where id = %s
        """, (  embedding.tolist(), document_id ))
        
        conn.commit()
        
    except Exception as e:
        conn.rollback()
        print(f"Error inserting chunks for document {document_id}: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

def fetch_document_embeddings():
    """Fetch all document embeddings from PostgreSQL"""
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    try:
        # Get documents with embeddings (assuming you have some metadata columns)
        cursor.execute("""
            SELECT id, embedding::real[], file_path  , title
            FROM documents 
            WHERE embedding IS NOT NULL
            ORDER BY id
        """)
        
        rows = cursor.fetchall()
        
        # Extract data
        doc_ids = [row[0] for row in rows]
        embeddings = np.array([row[1] for row in rows])  # Convert to numpy array
        filenames = [ str(row[0] ) + str(row[3] ) if row[3] else f"Document {row[0]}" for row in rows]
        titles = [ ( str(row[0]) + "," + str(row[2] ) ) if row[2] else f"doc_{row[0]}" for row in rows]
        
        print(f"Loaded {len(doc_ids)} document embeddings")
        print(f"Embedding shape: {embeddings.shape}")
        
        return doc_ids, embeddings,titles, filenames
    
    finally:
        cursor.close()
        conn.close()

def create_umap_projection(embeddings, n_neighbors=15, min_dist=0.1, random_state=42):
    """Create UMAP 2D projection of embeddings"""
    print("Creating UMAP projection...")
    
    # Initialize UMAP
    reducer = UMAP(
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        n_components=2,
        random_state=random_state,
        metric='cosine'  # Good for text embeddings
    )
    
    # Fit and transform
    embedding_2d = reducer.fit_transform(embeddings)
    
    print(f"UMAP projection complete. Shape: {embedding_2d.shape}")
    return embedding_2d, reducer

def cluster_documents(embedding_2d, min_cluster_size=5):
    """Cluster documents using HDBSCAN"""
    print("Clustering documents...")
    
    clusterer = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=1,
        metric='euclidean'
    )
    
    cluster_labels = clusterer.fit_predict(embedding_2d)
    
    n_clusters = len(set(cluster_labels)) - (1 if -1 in cluster_labels else 0)
    n_noise = list(cluster_labels).count(-1)
    
    print(f"Found {n_clusters} clusters")
    print(f"Noise points: {n_noise}")
    
    return cluster_labels, clusterer

def analyze_clusters(doc_ids, filenames, cluster_labels):
    """Analyze and print cluster information"""
            
    return pd.DataFrame({
        'doc_id': doc_ids,
        
        'filename': filenames,
        'cluster': cluster_labels
    })

def save_results(df, embedding_2d, save_prefix='topic_analysis'):
    """Save results for later analysis"""
    # Save cluster assignments
    df.to_csv(f'{save_prefix}_clusters.csv', index=False)
    
    # Save UMAP coordinates
    coords_df = pd.DataFrame({
        'doc_id': df['doc_id'],
        'umap_x': embedding_2d[:, 0],
        'umap_y': embedding_2d[:, 1],
        'cluster': df['cluster']
    })
    coords_df.to_csv(f'{save_prefix}_coordinates.csv', index=False)
    
    print(f"\nResults saved:")
    print(f"- {save_prefix}_clusters.csv")
    print(f"- {save_prefix}_coordinates.csv")
    print(f"- topic_map.png")



def clean_text_for_postgres(text):
    if not text: return ""
    text = text.replace('\x00', '')
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    return text.strip()

def clip_to_byte_limit(s, byte_limit):
    s_bytes = s.encode('utf-8')
    if len(s_bytes) <= byte_limit:
        return s
    return s_bytes[:byte_limit].decode('utf-8', errors='ignore')


def tf_idf():
    # Database connection
    conn = psycopg2.connect( **DB_CONFIG)
    cur = conn.cursor()
    cur.execute( "call refresh_topic_tables();" )
    conn.commit()
    cur.close()
    conn.close()

def terms():
    # Database connection
    conn = psycopg2.connect( **DB_CONFIG)
    cur = conn.cursor()
    cur.execute( "call simple_terms_parser();" )
    conn.commit()
    cur.close()
    conn.close()


def umap():
    # Step 1: Fetch embeddings from database
    doc_ids, embeddings, titles, filenames = fetch_document_embeddings()
    
    # Step 2: Create UMAP projection
    embedding_2d, reducer = create_umap_projection(embeddings)
    
    # Step 3: Cluster documents
    cluster_labels, clusterer = cluster_documents(embedding_2d)
    
    # Step 4: Visualize results
    #plot_topic_map(embedding_2d, titles, filenames,  cluster_labels)
    
    # Step 5: Analyze clusters
    df = analyze_clusters(doc_ids, filenames, cluster_labels)

    conn = psycopg2.connect(**DB_CONFIG)
    topics2documents = defaultdict( list )

    #df = pd.read_csv(r'topic_analysis_clusters.csv')
    for index, row in df.iterrows():
        topics2documents[row['cluster']].append( row['doc_id'])
        
    cursor = conn.cursor()
    # in order to make this setep idempotent, first delete from all these child tables
    # in reverse order . 
    # doc_coords, document_topics, topics.   
    cursor.execute( "delete from doc_coords" )
    cursor.execute( "delete from document_topics" )
    cursor.execute( "delete from topics" )
    
    for topic,documents in topics2documents.items():
        cursor.execute( "insert into topics ( title ) values ( 'default' )  returning id")
        new_topic_id = cursor.fetchone( )[0]
        for doc_id in documents:
            cursor.execute ( "insert into document_topics ( topic_id, document_id  ) values ( %s, %s ) ", ( new_topic_id, doc_id))
        conn.commit()
    conn.commit()
    cursor.close()
    
    
    
    # go ahead and store the other spread sheet in the db as well
    cursor = conn.cursor()
    
    coords_df = pd.DataFrame({
        'doc_id': df['doc_id'],
        'umap_x': embedding_2d[:, 0],
        'umap_y': embedding_2d[:, 1],
        'cluster': df['cluster']
    })
    

    for index, row in coords_df.iterrows():
        doc_id = row['doc_id']. item()
        x = row['umap_x'].item()
        y = row['umap_y'].item()
        cursor.execute( "insert into doc_coords ( document_id, x , y ) values ( %s, %s, %s)",(  doc_id, x, y ) )
    conn.commit()
    cursor.close()
    conn.close()
    
    return df, embedding_2d, cluster_labels

def embed_pdfs():

    """
    Dispatch one Celery task per unembedded document.
    Celery handles the concurrency (run workers with --concurrency=4).
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT id, raw_text FROM documents WHERE embedding IS NULL")
    documents = cursor.fetchall()
    cursor.close()
    conn.close()

    if not documents:
        print("No documents to embed.")
        return

    print(f"Dispatching {len(documents)} embedding tasks to Celery...")
    
    # Build a group so you can optionally wait/inspect results
    job = group(
        embed_single_document.s(doc_id, raw_text)
        for doc_id, raw_text in documents
        if raw_text  # skip empties
    )
    result = job.apply_async()
    
    # Optional: block and wait for all to finish before marking step complete
    # results = result.get(timeout=600)  # 10min overall timeout
    # print(f"All done: {results}")
    
    print(f"All {len(documents)} tasks dispatched. Celery is processing...")
   
    # print("Loading SentenceTransformer model...")
    # model = SentenceTransformer('all-MiniLM-L6-v2')
    
    # documents = get_documents()
    # print(f"Found {len(documents)} documents to process")
    
    # # Process each document
    # dox2embeddings = {}
    # for doc_id, raw_text in documents:
    #     print(f"\nProcessing document {doc_id}...")
        
    #     # Skip if raw_text is None or empty
    #     if not raw_text:
    #         print(f"Skipping document {doc_id} - no text content")
    #         continue
        
    #     # Chunk the text
    #     chunks = chunk_text(raw_text, chunk_size=100, overlap=10)
    #     print(f"Created {len(chunks)} chunks")
        
    #     embeddings  = model.encode( chunks )
    #     dox2embeddings[doc_id] = embeddings
    #     # Insert into database
    #     insert_chunked_embeddings(doc_id, chunks, embeddings )
    


    # print("\nAll documents processed successfully!")
    # #print( f"dox2embeddings --> {dox2embeddings}")
    # for doc_id , chunked_embeddings in dox2embeddings.items():
    #     doc_embedding = np.mean(chunked_embeddings,axis=0)
    #     update_doc_embedding( doc_id, doc_embedding  )

def pre_load_docs():
     print( 'about to pre load the pdf files ')
 
def process_pdfs():
    """The logic from your script integrated as a task"""
    print(f"Starting PDF scan in {DATA_FOLDER}...")
    
    try: 
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        # 1. Load existing files to avoid duplicates
        cur.execute('SELECT file_path FROM public.documents')
        existing_files = {row[0] for row in cur.fetchall()}
        print(f"Known files in DB: {len(existing_files)}")

        # 2. Walk the directory
        for root, dirs, files in os.walk(DATA_FOLDER):
            for filename in files:
                if filename.endswith(".pdf") and not filename.startswith("."):
                    path = os.path.join(root, filename)

                    if path in existing_files:
                        continue

                    print(f"Processing new file: {path}")
                    raw_text = ""
                    try:
                        with pdfplumber.open(path) as pdf:
                            for page in pdf.pages:
                                page_text = page.extract_text()
                                if page_text:
                                    # Encode/Decode to strip non-ascii as per your original script
                                    clean_page = page_text.encode('ascii', errors='ignore').decode('ascii')
                                    raw_text += clean_page + "\n\n<<PAGE_BREAK>>\n\n"
                        
                        if not raw_text.strip():
                            print(f"Skipping {filename}: No text found.")
                            continue

                        # 3. Clean and Save
                        cleaned = clean_text_for_postgres(raw_text)
                        clipped = clip_to_byte_limit(cleaned, MAX_BYTES - 1)

                        cur.execute(
                            "INSERT INTO public.documents (file_path, raw_text, title ) VALUES (%s, %s, %s) RETURNING id;",
                            (path, clipped, path)
                        )
                        new_id = cur.fetchone()[0]
                        conn.commit()
                        print(f"Inserted ID: {new_id}")

                    except Exception as e:
                        print(f"Error processing {filename}: {e}")
                        conn.rollback()

        cur.close()
        conn.close()
    except Exception as e:        
        print(f"Database connection error: {e}")



def start_celery():
    print( 'starting celery')
    
def stop_celery():
    print( 'ending celery' )
    
    
# --- MAIN WORKER LOOP ---
if __name__ == "__main__":
    r = redis.from_url(REDIS_URL)
    print("Python Worker is alive and listening for tasks...")

    while True:
        try:
            # Block until a task arrives
            task_data = r.blpop('python_tasks', timeout=0)
            job = json.loads(task_data[1])
            
            task_type = job.get("task")
            print(f"--- Received Task: {task_type} ---**********")

            if task_type == 'preload_docs': 
                pre_load_docs()
            elif task_type == "process_pdfs":
                process_pdfs()
            elif task_type == "embed_pdfs":
                embed_pdfs()
            elif task_type == "umap":
                umap()
            elif task_type == "terms":
                terms()
            elif task_type == "tf_idf":
                tf_idf()                
            elif task_type == "start_celery":
                start_celery()
            elif task_type == "stop_celery":
                stop_celery()
            else:
                print(f"Unknown task type: {task_type}")

            print("--- Task Complete ---")

        except Exception as e:
            print(f"Worker Loop Error: {e}")
            import traceback
            import sys
            traceback.print_exc( file=sys.stdout)
            time.sleep(2) # Prevent rapid fire looping on error



