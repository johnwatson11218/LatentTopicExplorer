import psycopg2
from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import RealDictCursor

from collections import defaultdict
from flask import Flask, render_template, url_for
#from functools import lru_cache
import math

app = Flask( __name__ ) 

def get_db_connection( 
    host: str = "rp",
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
def index():
    conn = get_db_connection()

    topics_and_documents = get_topics_and_documents( conn )
    serverData = get_document_coords( conn,  topics_and_documents )
    conn.close()
    return render_template( 'index.html',  topics_and_documents = topics_and_documents,  serverData = serverData )

@app.route( "/document/<id>")
def document( id ):
    conn = get_db_connection()
    document_data = get_document_data( conn , id )
    conn.close()
    return render_template( 'document.html', document_data = document_data[0] )

@app.route( "/page/<document_id>/<page_number>")
def page( document_id, page_number ):
    conn = get_db_connection()
    page_data = get_page_data( conn, document_id, page_number )
    conn.close()
    return render_template( 'page.html', document_id = document_id, page_number = page_number, page_data = page_data, prev_page_number = int(page_number) - 1, next_page_number = int(page_number) + 1 )

"""
<p><a href="/static/books/{{ document_data[1] }}">{{ document_data[1] }}</a></p>
"""


@app.route( "/topic/<id>")
def topic( id ):
    conn = get_db_connection()
    topic_data = get_topic_data( conn, id )
    serverData = get_document_coords( conn, get_topics_and_documents( conn ) ,  id  )
    conn.close()
    return render_template( 'topic.html', topic_data = topic_data, serverData = serverData )


def get_topic_data( conn, id ):
    cur = conn.cursor()
    cur.execute( """select d.id, d.filename, c.label, c.id from document_categories dc, documents d , categories c
                    where c.id = dc.category_id and dc.document_id = d.id and dc.category_id = %s""", ( id , ))
    rows = cur.fetchall()
    cur.close()
    return [ ( f"<a href='{url_for( 'document', id=r[0])}'>{r[1]}</a>", r[2], r[3] )  for r in rows ]   
 
def get_document_data( conn , id ):
    cur = conn.cursor()
    cur.execute( 'select d.id, d.filename, d.size, c.label, c.id, d.total_pages from documents d, document_categories dc, categories c where d.id = dc.document_id and dc.category_id = c.id and  d.id = %s', ( id, ))
    rows = cur.fetchall()    
    cur.close()
    return [ ( r[0], r[1], r[2], r[3] , r[4], r[5]) for r in rows ]

def get_page_data( conn, document_id, page_number ):
    cur = conn.cursor()
    cur.execute( 'select p.id, p.extracted_text, d.filename from pages p , documents d where p.document_id = d.id and  p.document_id = %s and page_number = %s ', ( document_id, page_number ))
    rows = cur.fetchall()
    cur.close()
    return { id : (text,filename) for (id,text,filename) in rows }


def get_topics_and_documents( conn ):
    cur = conn.cursor()
    cur.execute( """
                        select c.id , c.label , dc.color     from categories c, document_categories dc where c.id = dc.category_id group by c.id, c.label, dc.color order by 1 
                """)
    categories = [Category( r[0], r[1],r[2]) for r in cur.fetchall()] 
    [ c.get_documents( conn ) for c in categories ]
    cur.close()    
    return categories 

def get_document_coords( conn, topic_data = {} , topic_id = None ):
    try:
        plot_data = {
            "x": [], "y": [], "labels": [], 
            "o_sizes": [], "sizes": [], "colors": [], "ids" : []
        }

        for category in topic_data:            
            if topic_id is None or int(category.id) == int(topic_id):
                for document in category.documents:
                    plot_data['x'].append( document.x )
                    plot_data['y'].append( document.y )
                    plot_data['labels'].append( document.filename )
                    plot_data['ids'].append( document.id )
                    plot_data['o_sizes'].append( max( document.size, 0 ) )
                    plot_data['colors'].append( document.color )
        
        plot_data['sizes'] = [ ( math.sqrt(o) / 100 )  for o in plot_data['o_sizes']]                                    
        return plot_data

    except Exception as e:
        app.logger.info(f"General Error in get_document_coords: {e}")
        return None

class Document:
    def __init__(self, id, filename, file_size, inserted_at, embedding, size, logically_deleted, total_terms, total_pages, x, y, color ):
        self.id = id
        self.filename = filename 
        self.file_size = file_size
        self.inserted_at = inserted_at, 
        self.embedding = embedding
        self.size = size
        self.logically_deleted = logically_deleted
        self.total_terms = total_terms
        self.total_pages = total_pages
        self.pages = []
        self.x = x
        self.y = y
        self.color = color
        
    def get_pages( self, conn ):
        if self.pages == []:
            cur = conn.cursor()
            cur.execute("SELECT id, document_id, content, extracted_text, page_number, inserted_at, embedding, page_size FROM pages p where p.document_id = %s ", ( self.document_id , ))
            self.pages = [ Page( r[0], r[1], r[2], r[3],r[4], r[5], r[6], r[7]) for r in cur.fetchall() ]
            cur.close()             
        return self.pages
    
class Page:
    def __init__( self, id, document_id, content, extracted_text, page_number, inserted_at, embedding, page_size ):
        self.id = id
        self.document_id = document_id 
        self.content = content
        self.extracted_text = extracted_text
        self.page_number = page_number
        self.inserted_at = inserted_at
        self.embedding = embedding 
        self.page_size = page_size



class Category:
    def __init__( self, id, label, color ) :
        self.id = id
        self.label = label
        self.color = color 
        self.documents = []

    def get_documents( self, conn ):
        if self.documents == []:
            cur = conn.cursor()
            sql = """
                        select d.id, d.filename, d.file_size, d.inserted_at, d.embedding, d.size, d.logically_deleted, d.total_terms, d.total_pages, dcoords.x, dcoords.y, dc.color
                        from documents  d, document_categories dc , document_coordinates dcoords
                        where 
                        d.id = dc.document_id 
                        and dc.category_id = %s 
                        and d.id = dcoords.document_id
            """
            
            cur.execute( sql , (self.id, ))
            self.documents = [ Document ( r[0],r[1],r[2],r[3],r[4],r[5],r[6],r[7],r[8],r[9],r[10],r[11]) for r in cur.fetchall() ]
            cur.close()   
        return self.documents
    
    
