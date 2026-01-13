from flask import Flask, jsonify, render_template
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime


app = Flask(__name__)


def get_db_connection():
    # Use environment variables or hardcode the service name from docker-compose.yml
    conn = psycopg2.connect(
        host='postgres', # The name of your postgres service in docker-compose
        database='second_brain',
        user='postgres',
        password='test_case'
    )
    return conn

@app.route("/document/<id>")
def document_by_id( id ):
    sql = f" select id, file_path, left(  d.raw_text, 20000 ) || ' ...'  as preview , length( raw_text ) as len , embedding is null as embedded from documents d where d.id = {id}"
    
    con = get_db_connection()
    curr = con.cursor( cursor_factory=RealDictCursor)
    curr.execute( sql )
    doc_data = curr.fetchall()
    curr.close()
    con.close()
    return render_template( 'document.html', id=id, doc_data=doc_data )


@app.route("/")
def hello_world():
    app.logger.info('A user visited the index page.') # Log an informational message
    c = get_db_connection()
    count = get_count_docs()
    topic_data = get_topics_and_associated_documents()
    coords = get_document_coords( c )
    return render_template( 'index.html', count=count, topic_data=topic_data, date=datetime.datetime.now(), x = coords ) 


def get_topics_and_associated_documents():
    conn = get_db_connection()
    cur = conn.cursor( cursor_factory=RealDictCursor)
    sql = """
    WITH topic_titles AS (
        SELECT ttt.topic_id, 
               STRING_AGG( term_text, '-' ORDER BY rank DESC) AS label 
        FROM topic_top_terms ttt 
        GROUP BY ttt.topic_id
      ) 
      SELECT tt.label, 
             STRING_AGG( '<a href="/document/' || d.id || '">' || d.title || '</a>', ', ') AS document_titles
      FROM topic_titles tt, 
           document_topics dt, 
           documents d 
      WHERE d.id = dt.document_id 
        AND dt.topic_id = tt.topic_id 
      GROUP BY tt.label order by count( d.title ) desc;
    """
    cur.execute( sql )
    topic_data = cur.fetchall()
    cur.close()
    conn.close()
    return topic_data


def get_count_docs():
    conn = get_db_connection()
    
    # Use RealDictCursor to get results as "mapish" dictionaries
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # 1. Query for a single integer (e.g., a count)
    cur.execute('SELECT COUNT(*) FROM documents;')
    document_count = cur.fetchone()['count']


    cur.close()
    conn.close()

    return document_count
    # return jsonify({
    #     "total_docs": user_count,
    #     # "user_list": recent_users,
    #     # "products": active_products
    # })

def get_topic_document_mapping():    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)    
    sql = "select document_id , topic_id from document_topics "
    cur.execute( sql )
    return_data = {}
    for row in cur:
        return_data[ row['document_id']] = row['topic_id']
    cur.close()
    conn.close()

    return return_data


def get_document_coords(conn):
    try:
        # 1. Get the mapping first (replaces await getTopicDocumentMapping())
        topic_data = get_topic_document_mapping()

        colors = [
            'AliceBlue', 'Azure', 'Bisque', 'CadetBlue', 'BurlyWood', 'Coral', 'DarkCyan',
            'DarkKhaki', 'DarkOrange', 'DarkSlateBlue', 'Yellow', 'Violet', 'SteelBlue', 
            'Tan', 'Teal', 'SpringGreen', 'SlateGrey', 'Thistle', 'Tomato', 'Salmon', 
            'SandyBrown', 'SeaGreen'
        ]

        plot_data = {
            "x": [], "y": [], "labels": [], 
            "originalSizes": [], "sizes": [], "colors": [], "ids" : []
        }

        # 2. Fetch the document coordinates
        query = """
            SELECT 
                d.id AS document_id, 
                SUBSTRING(d.title FROM 1 FOR 20) AS title, 
                x, 
                y, 
                LENGTH(d.raw_text) AS size 
            FROM doc_coords dc
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
            size_val = row['size'] if row['size'] else 0
            plot_data['originalSizes'].append(size_val)

            # Match color based on the topic_data dict
            topic_id = topic_data.get(doc_id, 0)
            color_idx = topic_id % len(colors)
            plot_data['colors'].append(colors[color_idx])

        # 4. Normalize Sizes (The Math)
        # Replicating JS logic: scale = (max - min) / (50 - 10)
        orig_sizes = plot_data['originalSizes']
        if orig_sizes:
            min_s, max_s = min(orig_sizes), max(orig_sizes)
            range_s = max_s - min_s
            
            if range_s == 0:
                plot_data['sizes'] = [20] * len(orig_sizes)
            else:
                scale = range_s / 40
                for s in orig_sizes:
                    # formula: 5 + (size / scale)
                    plot_data['sizes'].append(5 + (s / scale))
        return plot_data

    except Exception as e:
        app.logger.info(f"General Error in get_document_coords: {e}")
        return None

if __name__ == "__main__":
    # Ensure the app runs on all available network interfaces
    app.run(host='0.0.0.0', port=5000) 

