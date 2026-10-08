import psycopg2
from psycopg2.extensions import connection as PGConnection
from pgvector.psycopg2 import register_vector

import io
from psycopg2.extras import execute_values
import os
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
import numpy as np

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

globalModel = None
def getLLModel():
    global globalModel
    if globalModel is None:
        globalModel = SentenceTransformer( 'all-MiniLM-L6-v2')
    return globalModel

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

def embed_single_page( page_id, conn ):
    register_vector( conn)
    cur = conn.cursor()
    cur.execute( "select extracted_text from pages where id = %s ", ( page_id, ))
    rows = cur.fetchall()
    if len( rows ) > 0:
        model = getLLModel()

    for (text,) in rows:
        vec = model.encode( text, normalize_embeddings=True )
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
            
            alter table documents add column if not exists total_terms int default 0; 

                update documents d set total_terms = sub.x from (
                    select p.document_id as document_id , sum( pt.count  ) as x   from page_terms pt, pages p
                    where p.id = pt.page_id 
                    group by p.document_id ) sub where d.id = sub.document_id ;

   
           
					insert into document_terms ( document_id, term_id, page_count, raw_count )  
						 select p.document_id as doc_id, ptl.term_id as term_id, count( ptl.page_id ) as page_count, sum( ptl.count ) as raw_count
						 from page_terms_llm ptl, pages p 
						 where 
						 	p.id = ptl.page_id 
							 group by p.document_id, ptl.term_id
            
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



if __name__ == "__main__":
    print( "running" )
    
    conn = get_db_connection()
    
    rows = claim_batch( conn , 'embed', 100000 )    
    print( f"number for embed text {len( rows )}")
    [ embed_single_page( page_id, conn ) for page_id in rows ]
    
    conn.close()
