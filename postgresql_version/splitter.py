import psycopg2
from psycopg2.extensions import connection as PGConnection
import io
from psycopg2.extras import execute_values
import os
from pypdf import PdfReader

import re

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

def mark_error(conn, id: int, step: str, error: str):
    cur = conn.cursor()
    col = 'page_id'
    if step == 'split':
        col = 'document_id'
    cur.execute(f" UPDATE pipeline_queue    SET status = 'error', error_msg = %s, updated_at = NOW()     WHERE {col} = %s AND step = %s ", (error, id, step))
    conn.commit()


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

def split_pdf_file_and_extract_text( document_id, conn  ):
    
    try:
        print( f"processing document {document_id}")
        cur = conn.cursor()
        sql = "select filename from documents where id = %s "
        cur.execute( sql , (document_id,) )
        path = cur.fetchone()[0]
    except Exception as e :
        mark_error( conn, document_id , 'split' , str( e ))
        return 

    with open(path, "rb") as f:
        blob = f.read()
        print( f"The blob is length = {len( blob )}")
        total_pages = 0
        try:
            reader = PdfReader( io.BytesIO( blob ))
            total_pages = len( reader.pages )
        except Exception as e:
            mark_error( conn, document_id , 'split' ,str(e))
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
                mark_error( conn, document_id , 'split', str( e ) )
                return
            conn.commit()
    mark_done(conn, document_id, 'split')

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
    
    conn = get_db_connection()
    
    
    rows = claim_batch(conn, 'split', 500 )
    [ split_pdf_file_and_extract_text( document_id, conn ) for document_id in rows  ]

    
    conn.close()
