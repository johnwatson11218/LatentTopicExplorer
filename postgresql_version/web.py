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
    serverData = get_document_coords( conn )
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

@app.route( "/topic/<id>")
def topic( id ):
    conn = get_db_connection()
    topic_data = get_topic_data( conn, id )
    serverData = get_document_coords( conn, id )
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

def get_documents( conn ):
    cur = conn.cursor()
    cur.execute( 'select id, filename from documents order by id  ' )
    rows = cur.fetchall()
    data_items = [ ( r[0], r[1]) for r in rows ]
    cur.close()
    return data_items

def get_topics_and_documents( conn ):
    cur = conn.cursor()
    cur.execute( "select c.id , c.label , dc.color     from categories c, document_categories dc where c.id = dc.category_id group by c.id, c.label, dc.color order by 1 ")
    categories = [Category( r[0], r[1],r[2]) for r in cur.fetchall()] 
    cur.close()    
    return { f"<a href='{url_for( 'topic', id=category.id )}'>{category.label}</a>" : # <-- this 'key' e.g. href to topic page  maps to a list of href links to each document in the topic 
                [ f"<a href='{url_for( 'document', id=document.id)}'>{document.filename}</a>" 
                    for document in category.get_documents( conn )    ] 
                        for category in categories }
    


def get_document_coords( conn, topic_id = None ):
    try:
        # this is map<String<Label>>, List<String<Filename>> . 
        topic_data = get_topics_and_documents( conn ) 
        plot_data = {
            "x": [], "y": [], "labels": [], 
            "o_sizes": [], "sizes": [], "colors": [], "ids" : []
        }

        query = """
            SELECT  d.id AS document_id, d.filename AS title, x, y , d.size AS size, 
            dcat.color as color             
            FROM document_coordinates dc, documents d, document_categories dcat
            where d.id = dc.document_id and d.logically_deleted is false
            and dcat.document_id = d.id            
        """            
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if topic_id is None:
                cur.execute(query)
            else:
                query += f" and dcat.category_id = %s"
                cur.execute( query, ( topic_id, ))
                
            rows = cur.fetchall()

        for row in rows:
            doc_id = row['document_id']
            plot_data['x'].append(row['x'])
            plot_data['y'].append(row['y'])
            plot_data['labels'].append(row['title'].split('/')[-1].split('\\')[-1])
            plot_data['ids'].append( doc_id )

            plot_data['o_sizes'].append( row['size'])            
            plot_data['colors'].append( row['color'])

        ( min_size, max_size ) = ( min(plot_data['o_sizes'] ), max( plot_data['o_sizes']) )
        scale_factor = ( max_size - min_size  ) / 100        
        plot_data['sizes'] = [ ( math.sqrt(o) / 100 )  for o in plot_data['o_sizes']]
                    
        cur.close()

        return plot_data

    except Exception as e:
        app.logger.info(f"General Error in get_document_coords: {e}")
        return None


##############################################################################################################################
""" Problem right now is that there are only a few endpoints and data access methods but already there are multiple
    My inital thought was to have AI generate a  DAO layer using SQLAlchemy but already that was a lot of mental overhead on my part.
    There were new config files and choices to made about the kind of layer .... never mind that. 
    New plan is to create simple OO style python classes for my tables and not be dogmatic about it. 
    Hey why not create a functional style immutable OO design here , with lazy loaded attributes and child collections. 
    Then for the back end code make a new OO map that contains all that old-style error prone state modification code. 
"""
#############################################################################################################################


class Document:
    def __init__(self, id, filename, file_size, inserted_at, embedding, size, logically_deleted, total_terms, total_pages ):
        self.id = id
        self.filename = filename 
        self.file_size = file_size
        self.inserted_at = inserted_at
        self.size = size
        self.logically_deleted = logically_deleted
        self.total_terms = total_terms
        self.total_pages = total_pages
        self.pages = []
        
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


# will also need categories c, document_categories dc, just to render the home page .

class Category:
    def __init__( self, id, label, color ) :
        self.id = id
        self.label = label
        self.color = color 
        self.documents = []

    def get_documents( self, conn ):
        if self.documents == []:
            cur = conn.cursor()
            cur.execute( "select d.id, d.filename, d.file_size, d.inserted_at, d.embedding, d.size, d.logically_deleted, d.total_terms, d.total_pages from documents  d, document_categories dc where d.id = dc.document_id and dc.category_id = %s ", (self.id, ))
            self.documents = [ Document ( r[0],r[1],r[2],r[3],r[4],r[5],r[6],r[7],r[8],) for r in cur.fetchall() ]
            cur.close()   
        return self.documents
    
    
