
import sqlite3
import os
from pathlib import Path
from typing import List, Tuple, Optional
from pypdf import PdfReader, PdfWriter

def init_db(db_path: str = "app_data.db") -> sqlite3.Connection:
    """Open (or create) the SQLite database and set up tables."""
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)   # create folder if missing

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Table 1: parameters (name → float value)
    # name is TEXT so it's flexible (you can use single chars or longer keys)
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
                    inserted_at timestamp default current_timestamp                
                    ) 
                """)
    
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
    pass            
            
conn = init_db()
scan_folder( conn, "data" )

split_pdf_files()