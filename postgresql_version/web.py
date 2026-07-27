import psycopg2
from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import RealDictCursor

from collections import defaultdict
from flask import Flask, render_template
#from functools import lru_cache
import math
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

    topics_and_documents = get_topics_and_documents( conn )
    serverData = get_document_coords( conn )
    conn.close()
    return render_template( 'index.html',  topics_and_documents = topics_and_documents,  serverData = serverData )



def get_documents( conn ):
    cur = conn.cursor()
    cur.execute( 'select id, filename from documents order by id  ' )
    rows = cur.fetchall()
    data_items = [ ( r[0], r[1]) for r in rows ]
    cur.close()
    return data_items

    
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

def get_document_coords( conn ):
    try:
        #conn = get_db_connection()
        topic_data = get_topics_and_documents( conn ) # this is map<String<Label>>, List<String<Filename>> . 

        #colors = get_topic_colors(len( set( topic_data.values())))
        # colors_old = [
        #     'AliceBlue', 'Azure', 'Bisque', 'CadetBlue', 'BurlyWood', 'Coral', 'DarkCyan',
        #     'DarkKhaki', 'DarkOrange', 'DarkSlateBlue', 'Yellow', 'Violet', 'SteelBlue', 
        #     'Tan', 'Teal', 'SpringGreen', 'SlateGrey', 'Thistle', 'Tomato', 'Salmon', 
        #     'SandyBrown', 'SeaGreen'
        # ]

        plot_data = {
            "x": [], "y": [], "labels": [], 
            "o_sizes": [], "sizes": [], "colors": [], "ids" : []
        }

        # 2. Fetch the document coordinates
        query = """
            SELECT  d.id AS document_id, SUBSTRING(d.filename FROM 1 FOR 20) AS title, x, y , d.size AS size, 
            dcat.color as color             
            FROM document_coordinates dc, documents d, document_categories dcat
            where d.id = dc.document_id and d.logically_deleted is false
            and dcat.document_id = d.id
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            rows = cur.fetchall()

        if not rows:
            return plot_data

        # 3. Process data & assign colors
        for row in rows:
            doc_id = row['document_id']
            plot_data['x'].append(row['x'])
            plot_data['y'].append(row['y'])
            plot_data['labels'].append(row['title'])
            plot_data['ids'].append( doc_id )

            plot_data['o_sizes'].append( row['size'])            
            plot_data['colors'].append( row['color'])

        ( min_size, max_size ) = ( min(plot_data['o_sizes'] ), max( plot_data['o_sizes']) )
        scale_factor = ( max_size - min_size  ) / 100        
        plot_data['sizes'] = [ ( math.sqrt(o) / 100 )  for o in plot_data['o_sizes']]
                    
        cur.close()
        #conn.close()                    
        return plot_data

    except Exception as e:
        app.logger.info(f"General Error in get_document_coords: {e}")
        return None
