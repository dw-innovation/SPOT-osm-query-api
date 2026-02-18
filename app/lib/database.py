import psycopg2
import psycopg2.extras
import psycopg2.pool
from flask import g

"""
Database connection pool utilities using psycopg2 and Flask `g`.

This module provides functions to:
- Initialize a PostgreSQL threaded connection pool.
- Retrieve a connection from the pool (Flask request context).
- Return the connection to the pool when the request ends.
"""

db_pool = None


def initialize_connection_pool(database_config):
    """Initialize a PostgreSQL connection pool using the provided config.

    Args:
        database_config (dict): Database configuration with keys:
            - "user": Username for the DB
            - "password": Password for the DB
            - "host": Hostname or IP address
            - "port": Port number
            - "name": Name of the database

    Side Effects:
        Initializes a global `db_pool` with a `ThreadedConnectionPool`
        (minconn=1, maxconn=10).

    Raises:
        psycopg2.DatabaseError: If the connection fails during pool initialization.
    """
    global db_pool
    db_pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        user=database_config["user"],
        password=database_config["password"],
        host=database_config["host"],
        port=database_config["port"],
        database=database_config["name"],
    )


def get_db():
    """Retrieve a database connection from the connection pool.

    This function is designed for use within a Flask request context. The
    connection is stored in `flask.g` so that it's reused throughout the request.

    Returns:
        psycopg2.extensions.connection: A connection object from the pool.
    """
    if "db" not in g:
        g.db = db_pool.getconn()
    return g.db


def close_db(e=None):
    """Release the database connection at the end of a Flask request.

    Args:
        e (Optional[Exception]): Flask passes an optional exception object
        if the request encountered an error. It is unused here.

    Side Effects:
        Removes the connection from `flask.g` and returns it to the pool
        using `db_pool.putconn()`.
    """
    db = g.pop("db", None)
    if db is not None:
        db_pool.putconn(db)
