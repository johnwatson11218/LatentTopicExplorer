
import re
import sqlite3
import os
from collections import defaultdict
import nltk
from nltk.stem import PorterStemmer
from nltk.corpus import stopwords

from pathlib import Path
from typing import List, Tuple, Optional
from pypdf import PdfReader, PdfWriter
import io

def init_db(db_path: str = "app_data.db") -> sqlite3.Connection:
    """Open (or create) the SQLite database and set up tables."""
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)   # create folder if missing

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(""" CREATE TABLE IF NOT EXISTS documents 
                ( 
                    id integer primary key,  
                    filename   TEXT not null,
                    content BLOB, 
                    file_size integer,
                    inserted_at timestamp default current_timestamp
                    
                    ) 
                """)
    
    cur.execute(""" CREATE TABLE IF NOT EXISTS pages 
                ( 
                    id integer primary key,  
                    document_id integer,
                    content BLOB, 
                    extracted_text text,
                    page_number integer not null,
                    inserted_at timestamp default current_timestamp                
                    ) 
                """)
    
    cur.execute( " create table if not exists  terms  ( id integer primary key, term text not null unique ) ")
    
    cur.execute( """
                create table if not exists document_terms (
                    id integer primary key, 
                    document_id integer not null references doccuments( id), 
                    term_id integer not null references terms( id ) , 
                    tf real , -- term frequencey ( count / total_terms_in_doc )
                    raw_count integer , -- how many times the term appears in this doc
                    page_count integer, -- how many pages it appears on
                    unique( document_id , term_id )                
                )""")
    conn.commit()
    print(f"✅ Database ready: {db_path} ({db_file.stat().st_size} bytes)")
    return conn

def scan_folder(  conn = None, file_path : str = "data" ) -> None:
    print( f"starting scan file_path ={file_path}, conn {conn}")
    for root, dirs, files in os.walk(file_path):        
        #print( f"root = {root} len(dirs ) {len(dirs)} len(files) {len(files)}")
        for filename in files:
            cur = conn.cursor()
            
            if filename.endswith(".pdf") and not filename.startswith("."):
                try:
                    
                    path = os.path.join(root, filename)
                    with open(path, "rb") as f:
                        pdf_bytes = f.read()

                    cur.execute("insert into documents ( filename, content, file_size ) values ( ?,?,? )", (filename,pdf_bytes, len(pdf_bytes)) )
                    conn.commit()
                except Exception as e:
                    print( f"Got an error {e}")
            
def split_pdf_files( conn = None ):
    
    cur = conn.cursor()
    cur.execute( " select d.id from documents d where not exists ( select 1 from pages p where p.document_id = d.id ) ")
    ids = cur.fetchall()
    print( f"There are {len( ids )} documents to split_pdf_files ... ")
    
    for row in ids:
        document_id = row[0]
        print( f"processing document {document_id}")
        sql = "select content from documents where id = ? "
        cur.execute( sql , (document_id,) )
        blob = cur.fetchone()[0]

        reader = PdfReader( io.BytesIO( blob ))
        for page_num in range(len(reader.pages)):
            writer = PdfWriter()
            writer.add_page(reader.pages[page_num])            
            output_buffer = io.BytesIO()
            writer.write( output_buffer )
            page_blob = output_buffer.getvalue()
            page_sql = "insert into pages ( document_id , content, page_number ) values ( ?,?,? )"
            cur.execute(page_sql , (document_id , page_blob, page_num ))
            output_buffer.close()
            conn.commit()
       
    cur.close()
    
  
def extract_text_from_stored_pages( conn = None ):
    cur = conn.cursor()
    cur.execute( "select id, content from pages where content is not null and extracted_text is null ") 
    rows = cur.fetchall()
    print(f"found {len(rows)} pages to attempt to extract text from")
    for row in rows:
        page_id, page_blob = row
        try:
            pass
            reader = PdfReader( io.BytesIO( page_blob ))
            page = reader.pages[0]
            raw_text = page.extract_text(extraction_mode='layout')
            update_cur = conn.cursor()
            update_cur.execute( "update pages set extracted_text = ? where id = ? ", (raw_text, page_id))
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
              AND document_id = ?
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
            doc_cursor.execute('INSERT OR IGNORE INTO terms (term) VALUES (?)', (term,))
            if doc_cursor.lastrowid:
                term_id = doc_cursor.lastrowid
            else:
                doc_cursor.execute('SELECT id FROM terms WHERE term = ?', (term,))
                term_id = doc_cursor.fetchone()[0]

            tf = stats['count'] / total_tokens if total_tokens else 0
            doc_cursor.execute("""
                INSERT INTO document_terms (document_id, term_id, raw_count, tf, page_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(document_id, term_id) DO UPDATE SET
                    raw_count  = excluded.raw_count,
                    tf         = excluded.tf,
                    page_count = excluded.page_count
            """, (document_id, term_id, stats['count'], tf, len(stats['pages'])))

        conn.commit()
        doc_cursor.close()

    cur.close()
    print('end populate_terms()')
    
###########################################################################################################    
conn = init_db()
scan_folder( conn, "data" )
split_pdf_files( conn )
extract_text_from_stored_pages( conn )
populate_terms(conn )

conn.commit()
conn.close()