import os
import numpy as np
import psycopg2
from celery import Celery
from sentence_transformers import SentenceTransformer
from celery.utils.log import get_task_logger
import pdfplumber
import re

MAX_BYTES = 1048575
DATA_FOLDER = "data/"

logger = get_task_logger(__name__)

REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379')
DB_CONFIG = {
    "dbname": "second_brain",
    "user": "postgres",
    "password": "test_case",
    "host": "postgres",
    "port": 5432
}

# Celery listens on a dedicated queue so it doesn't conflict with your existing worker
app = Celery('documents', broker=REDIS_URL, backend=REDIS_URL)
app.conf.task_queues_default_exchange = 'celery'
app.conf.task_default_queue = 'parse_docs'
app.conf.task_serializer = 'json'
app.conf.result_serializer = 'json'
app.conf.accept_content = ['json']

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



@app.task(
    bind=True,
    name='parse_single_document',
    queue='parse_docs',
    max_retries=3,
    soft_time_limit=12000,   # raises SoftTimeLimitExceeded after 2min (nice-to-have)
    time_limit=15000,         # hard kill after 2.5min
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def read_and_parse_single_file( self,  path ):
    print(f"Processing new file: {path}")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
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
            print(f"Skipping {path}: No text found.")
            return

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
        print(f"Error processing {path}: {e}")
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

        
