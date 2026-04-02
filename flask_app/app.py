from flask import Flask, jsonify, render_template, redirect , url_for, flash
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime
from redis import Redis
import json
import matplotlib.pyplot as plt
import numpy as np

from functools import lru_cache

app = Flask(__name__)
redis_client = Redis(host='redis', port=6379, db=0)
app.secret_key = 'use_with_caution.'
DATA_VERSION = 1

@app.route("/")
def hello_world():
    app.logger.info('A user visited the index page.') 
    
    _topic_data = get_topics_and_associated_documents()
    
    from  collections import defaultdict
    x_map = defaultdict(list )
    [ x_map[ "<a href='" + url_for('topic_by_id', id=td['id']) + "'>" + td['label'] + "</a>"].append(
                    "<a href='" + url_for( 'document_by_id', id=td['doc_id'] ) + "'>" + td['document_titles'] + "</a>"
            ) for td in _topic_data 
    ]
    
    return render_template( 'index.html', count=get_count_docs(), topic_data=_topic_data, x = get_document_coords(  ),x_map=x_map )

@app.route("/document/<id>")
def document_by_id( id ):
    sql = f" select id, file_path, left(  d.raw_text, 20000 ) || ' ...'  as preview , length( raw_text ) as len , embedding is not null as embedded from documents d where d.id = {id}"
    
    con = get_db_connection()
    curr = con.cursor( cursor_factory=RealDictCursor)
    curr.execute( sql )
    doc_data = curr.fetchall()
    curr.close()
    con.close()
    return render_template( 'document.html', id=id, doc_data=doc_data )


@app.route( "/topic/<id>")
def topic_by_id( id ) :
    #topic_data = { 'doc_ids' : [ 1, 2, 3 ]}
    topic_data = get_topics_and_associated_documents( id )
    return render_template( 'topic.html', id=id, topic_data=topic_data )

@app.route("/load_docs")
def load_docs():
    app.logger.info( "A user clicked load_docs.")
    redis_client.rpush( 'python_tasks', json.dumps( {'task' : 'process_pdfs' } ))
    flash("Loading docs.")
    return redirect(url_for('hello_world'))

@app.route("/embed_docs")
def embed_docs():
    app.logger.info( "A user clicked /embed_docs.")
    redis_client.rpush( 'python_tasks', json.dumps( {'task' : 'embed_pdfs' } ))
    flash("Embedding docs.")
    return redirect(url_for('hello_world'))


@app.route("/umap")
def umap():
    app.logger.info( "A user clicked /umap")
    redis_client.rpush( 'python_tasks', json.dumps( {'task' : 'umap' } ))
    flash("Umap.")
    return redirect(url_for('hello_world'))

@app.route("/terms")
def terms():
    app.logger.info( "A user clicked /terms")
    redis_client.rpush( 'python_tasks', json.dumps( {'task' : 'terms' } ))
    flash("terms.")
    return redirect(url_for('hello_world'))

@app.route("/tf_idf")
def tf_idf():
    app.logger.info( "A user clicked /tf_idf")
    redis_client.rpush( 'python_tasks', json.dumps( {'task' : 'tf_idf' } ))
    flash("tf_idf.")
    return redirect(url_for('hello_world'))

@app.route("/clear_cache")
def clear_cache():
    app.logger.info("A user clicked /clear_cache")
    global DATA_VERSION
    DATA_VERSION = DATA_VERSION + 1 
    flash( f"Clear cache version at {DATA_VERSION}")
    return redirect( url_for( 'hello_world'))
  


@lru_cache(maxsize=128)
def get_topics_and_associated_documents(topic_id=None):
    return _get_topics_and_associated_documents( DATA_VERSION, topic_id )

def _get_topics_and_associated_documents( version , topic_id=None ):
    conn = get_db_connection()
    cur = conn.cursor( cursor_factory=RealDictCursor)
    
    sql = """
        select t.title as label, d.id as doc_id, d.title as document_titles, dt.topic_id as id  from documents d, document_topics dt, topics t where t.id = dt.topic_id and dt.document_id = d.id 
    """
    
    if topic_id is not None:
        sql += " and dt.topic_id = " + topic_id 


    cur.execute( sql )
    topic_data = cur.fetchall()
    cur.close()
    conn.close()
    return topic_data

@lru_cache(maxsize=128)
def _get_count_docs(version : int):
    return len( get_topics_and_associated_documents())

def get_count_docs() -> int:
    return _get_count_docs(DATA_VERSION)

@lru_cache(maxsize=128)
def get_topic_document_mapping( ):
    return _get_topic_document_mapping( DATA_VERSION )

def _get_topic_document_mapping( version : int):    
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

@lru_cache(maxsize=128)
def get_document_coords():
    return _get_document_coords(DATA_VERSION)

def _get_document_coords( version : int):
    try:
        conn = get_db_connection()
        topic_data = get_topic_document_mapping()

        colors = get_topic_colors(len( set( topic_data.values())))
        colors_old = [
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
                    
                    
        cur.close()
        conn.close()                    
        return plot_data

    except Exception as e:
        app.logger.info(f"General Error in get_document_coords: {e}")
        return None

@lru_cache(maxsize=128)
def get_topic_colors(n_topics):
    return _get_topic_colors( DATA_VERSION, n_topics)

def _get_topic_colors(version : int , n_topics):
    """Generate n_topics distinct colors using ColorBrewer"""
    colors = []
    
    # Use multiple ColorBrewer sets
    set1 = plt.cm.Set1(np.linspace(0, 1, 9))[:8]  # Skip gray
    set2 = plt.cm.Set2(np.linspace(0, 1, 8))
    set3 = plt.cm.Set3(np.linspace(0, 1, 12))
    dark2 = plt.cm.Dark2(np.linspace(0, 1, 8))
    paired = plt.cm.Paired(np.linspace(0, 1, 12))
    
    all_colors = np.vstack([set1, set2, set3, dark2, paired])
    
    # Convert to hex for web
    hex_colors = [
        '#{:02x}{:02x}{:02x}'.format(
            int(c[0]*255), int(c[1]*255), int(c[2]*255)
        ) 
        for c in all_colors[:n_topics]
    ]
    
    return hex_colors


def get_db_connection():
    # Use environment variables or hardcode the service name from docker-compose.yml
    conn = psycopg2.connect(
        host='postgres', # The name of your postgres service in docker-compose
        database='second_brain',
        user='postgres',
        password='test_case'
    )
    return conn

if __name__ == "__main__":
    # Ensure the app runs on all available network interfaces
    app.run(host='0.0.0.0', port=5000) 

