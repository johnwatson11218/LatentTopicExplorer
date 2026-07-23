import psycopg2
from psycopg2.extensions import connection as PGConnection
from collections import defaultdict
from flask import Flask, render_template
from functools import lru_cache

app = Flask( __name__ ) 

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

@app.route( "/" )
def hello_world():
    conn = get_db_connection()
    data_items = get_documents( conn )
    topics_and_documents = get_topics_and_documents( conn )
    conn.close()
    return render_template( 'index.html', data_items = data_items, topics_and_documents = topics_and_documents )


@lru_cache(maxsize=128)
def get_documents( conn ):
    cur = conn.cursor()
    cur.execute( 'select id, filename from documents order by id  ' )
    rows = cur.fetchall()
    data_items = [ ( r[0], r[1]) for r in rows ]
    cur.close()
    return data_items

@lru_cache(maxsize=128)
def get_topics_and_documents( conn ):
    cur = conn.cursor()
    cur.execute( """
                select c.label, d.filename from categories c, document_categories dc, documents d
                    where c.id = dc.category_id and dc.document_id = d.id
                    order by 1 
                """)
    rows = cur.fetchall()
    topics_and_documents = defaultdict( list )
    [ topics_and_documents[r[0]].append( r[1]) for r in rows ]        
    cur.close()
    return topics_and_documents
    

