import psycopg2
from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import RealDictCursor

from collections import defaultdict
from flask import Flask, render_template
from functools import lru_cache

app = Flask( __name__ ) 

DATA_VERSION = 1

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
    serverData = get_document_coords( conn )
    conn.close()
    return render_template( 'index.html', data_items = data_items, topics_and_documents = topics_and_documents,  serverData = serverData )


@lru_cache(maxsize=128)
def get_documents( conn ):
    return _get_documents( DATA_VERSION, conn )

def _get_documents( version, conn ):
    cur = conn.cursor()
    cur.execute( 'select id, filename from documents order by id  ' )
    rows = cur.fetchall()
    data_items = [ ( r[0], r[1]) for r in rows ]
    cur.close()
    return data_items

@lru_cache(maxsize=128)
def get_topics_and_documents( conn ):
    return _get_topics_and_documents( DATA_VERSION, conn )
    
def _get_topics_and_documents( version, conn ):
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
    

@lru_cache(maxsize=128)
def get_document_coords( conn):
    return _get_document_coords(DATA_VERSION, conn)

def _get_document_coords( version : int, conn ):
    try:
        #conn = get_db_connection()
        topic_data = get_topics_and_documents( conn )

        #colors = get_topic_colors(len( set( topic_data.values())))
        # colors_old = [
        #     'AliceBlue', 'Azure', 'Bisque', 'CadetBlue', 'BurlyWood', 'Coral', 'DarkCyan',
        #     'DarkKhaki', 'DarkOrange', 'DarkSlateBlue', 'Yellow', 'Violet', 'SteelBlue', 
        #     'Tan', 'Teal', 'SpringGreen', 'SlateGrey', 'Thistle', 'Tomato', 'Salmon', 
        #     'SandyBrown', 'SeaGreen'
        # ]

        plot_data = {
            "x": [], "y": [], "labels": [], 
            "originalSizes": [], "sizes": [], "colors": [], "ids" : []
        }

        # 2. Fetch the document coordinates
        query = """
            SELECT 
                d.id AS document_id, 
                SUBSTRING(d.filename FROM 1 FOR 20) AS title, 
                x, 
                y --, LENGTH(d.raw_text) AS size 
            FROM document_coordinates dc
            JOIN documents d ON d.id = dc.document_id
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
            #size_val = row['size'] if row['size'] else 0
            #plot_data['originalSizes'].append(size_val)

            # Match color based on the topic_data dict
            topic_id = topic_data.get(doc_id, 0)
            #color_idx = topic_id % len(colors)
            #plot_data['colors'].append(colors[color_idx])

        # 4. Normalize Sizes (The Math)
        # Replicating JS logic: scale = (max - min) / (50 - 10)
        #orig_sizes = plot_data['originalSizes']
        # if orig_sizes:
        #     min_s, max_s = min(orig_sizes), max(orig_sizes)
        #     range_s = max_s - min_s
            
        #     if range_s == 0:
        #         plot_data['sizes'] = [20] * len(orig_sizes)
        #     else:
        #         scale = range_s / 40
        #         for s in orig_sizes:
        #             # formula: 5 + (size / scale)
        #             plot_data['sizes'].append(5 + (s / scale))
                    
                    
        cur.close()
        conn.close()                    
        return plot_data

    except Exception as e:
        app.logger.info(f"General Error in get_document_coords: {e}")
        return None
