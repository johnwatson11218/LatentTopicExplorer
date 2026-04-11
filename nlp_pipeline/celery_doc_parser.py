import os
import numpy as np
import psycopg2
from celery import Celery
from celery.utils.log import get_task_logger
import pdfplumber
import re

MAX_BYTES = 1048575
DATA_FOLDER = "data/"

logger = get_task_logger(__name__)

REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379')
DB_CONFIG = {
    "dbname": os.getenv( 'DB_NAME', "second_brain" ),
    "user": os.getenv( 'DB_USER', "postgres" ),
    "password": os.getenv( 'DB_PASSWORD', "test_case" ),
    "host": os.getenv( 'DB_HOST', "postgres" ),
    "port": os.getenv( 'DB_PORT', 5432 )
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
    soft_time_limit=1200,   # raises SoftTimeLimitExceeded after 20min (nice-to-have)
    time_limit=1500,         # hard kill after 22.5min
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def read_and_parse_single_file( self,  path ):
    print(f"Processing new file: {path}")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    #raw_text = ""
    try:
        
        
        with open(path, "rb") as f:
            pdf_bytes = f.read()
        cur.execute( "INSERT INTO public.documents (file_path, raw_text, title, pdf_data ) VALUES (%s, %s, %s, %s ) RETURNING id;", (path, '', path, pdf_bytes))
        new_id = cur.fetchone()[0]
        conn.commit()
        
        with pdfplumber.open(path) as pdf:
            sq = 0
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    sq = sq + 1
                    # Encode/Decode to strip non-ascii as per your original script
                    clean_page = page_text.encode('ascii', errors='ignore').decode('ascii')
                    #raw_text += clean_page + "\n\n<<PAGE_BREAK>>\n\n"
                    cur.execute( 'insert into public.pages ( input_text, sequence_number, document_id ) values ( %s, %s, %s ) ', ( clean_page, sq , new_id))
                    conn.commit()

        # 3. Clean and Save
        # cleaned = clean_text_for_postgres(raw_text)
        # clipped = clip_to_byte_limit(cleaned, MAX_BYTES - 1)

        conn.commit()
        print(f"Inserted ID: {new_id}")

    except Exception as e:
        print(f"Error processing {path}: {e}")
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

        
"""
-- after all the documents are loaded don't forget to run sql like this so that subsequent steps in the pipeline don't
-- need to be modified. 
with all_text as ( 
select p.document_id id ,  string_agg( input_text, '' order by sequence_number )  text   from pages p group by p.document_id 
)
update documents d set raw_text = a.text from all_text a where a.id = d.id ;
"""

