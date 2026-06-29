import psycopg2
from psycopg2.extensions import connection as PGConnection

from flask import Flask

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
    cur = conn.cursor()
    cur.execute( 'select id, filename from documents ' )
    rows = cur.fetchall()
    data_items = [ ( r[0], r[1] ) for r in rows ]
    
            
    return f"<p>Hello, World! data is ${data_items}</p>"

