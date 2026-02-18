import json
import os
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from jsonschema import validate as validate_json_schema, exceptions
import psycopg2
from psycopg2 import DatabaseError, ProgrammingError, InterfaceError, OperationalError
from psycopg2.extensions import QueryCanceledError
from lib.ctes.construct_search_area import (
    AreaInvalidError,
)
from lib.utils import (
    get_spots,
    set_area,
    results_to_geojson,
    check_area_surface,
    validate_spot_query,
    clean_spot_query,
)
from lib.database import initialize_connection_pool, get_db, close_db
import lib.constructor as constructor
from collections import Counter
from lib.timer import Timer
from flask_compress import Compress
from dotenv import load_dotenv
import jwt
from jwt import InvalidTokenError

load_dotenv()

def check_env_vars():
    """
    Ensure all required environment variables are present.

    Raises:
        EnvironmentError: If one or more required variables are missing.

    Required vars:
        DATABASE_NAME, DATABASE_USER, DATABASE_PASSWORD, DATABASE_HOST, DATABASE_PORT,
        TABLE_VIEW, JWT_SECRET, TIMEOUT
    """
    required_vars = [
        "DATABASE_NAME",
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "DATABASE_HOST",
        "DATABASE_PORT",
        "TABLE_VIEW",
        "JWT_SECRET",
        "TIMEOUT"
    ]
    missing_vars = [var for var in required_vars if os.getenv(var) is None]
    if missing_vars:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

check_env_vars()

environment = os.getenv("ENVIRONMENT") or "production"

compress = Compress()
app = Flask(__name__)
compress.init_app(app)
CORS(app)

DATABASE = {
    "name": os.getenv("DATABASE_NAME"),
    "user": os.getenv("DATABASE_USER"),
    "password": os.getenv("DATABASE_PASSWORD"),
    "host": os.getenv("DATABASE_HOST"),
    "port": os.getenv("DATABASE_PORT"),
}

JWT_SECRET = os.getenv("JWT_SECRET")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() in ["true", "1", "yes"]

with open("./schemas/spot_query.json", "r") as file:
    schema = json.load(file)

@app.before_first_request
def setup():
    """
    Initialize the database connection pool before the first request.
    """
    initialize_connection_pool(DATABASE)

@app.teardown_appcontext
def teardown(e=None):
    """
    Close/return database connections at the end of the app context.

    Args:
        e (Exception | None): Optional teardown exception provided by Flask.
    """
    close_db(e)

def validate_jwt(token):
    """
    Validate a JWT using the configured secret and HS256 algorithm.

    Args:
        token (str): The bearer token string (without the 'Bearer ' prefix).

    Returns:
        dict: The decoded JWT payload if validation succeeds.

    Raises:
        ValueError: If the token is invalid or expired.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload
    except InvalidTokenError as e:
        raise ValueError(f"Invalid token: {str(e)}")

@app.before_request
def check_jwt():
    """
    Enforce JWT authentication on incoming requests when AUTH_ENABLED is true.

    Behavior:
        - Expects an 'Authorization: Bearer <token>' header.
        - On missing/invalid token, returns a 401 JSON error response.
        - Skips authentication for Flask's 'static' endpoint.

    Returns:
        flask.Response | None: 401 response on failure; None to continue on success.
    """
    if AUTH_ENABLED and request.endpoint not in ['static']:
        auth_header = request.headers.get("Authorization")
        if auth_header is None or not auth_header.startswith("Bearer "):
            return jsonify({"status": "error", "message": "Missing or invalid token"}), 401
        
        token = auth_header.split(" ")[1]
        try:
            validate_jwt(token)
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 401

@app.route("/validate-spot-query", methods=["POST"])
def validate_spot_query_route():
    """
    Validate a spot query JSON payload against the JSON Schema and custom rules.

    Expects:
        JSON body matching ./schemas/spot_query.json plus extra semantic checks
        applied by `validate_spot_query`.

    Returns:
        (flask.Response, int): 200 with {"status":"success"} on success;
        400 with {"status":"error","errorType":"spot_queryInvalid","message": "..."} on failure.
    """
    data = request.json

    try:
        validate_json_schema(data, schema)
        validate_spot_query(data)

        return jsonify({"status": "success"}), 200
    except (exceptions.ValidationError, ValueError) as e:
        return (
            jsonify(
                {"status": "error", "errorType": "spot_queryInvalid", "message": str(e)}
            ),
            400,
        )

@app.route("/get-pg-query", methods=["POST"])
def get_spot_query_route():
    """
    Build and return the generated PostgreSQL query for a valid spot query.

    Process:
        1) Validate input JSON (schema + custom checks).
        2) Clean the spot query.
        3) Set/derive the search area in Flask `g`.
        4) Construct the SQL using `constructor.construct_query_from_graph`.
        5) Return the SQL string (cursor.as_string).

    Returns:
        str | flask.Response: SQL query string on success; JSON error response otherwise.

    Errors:
        422 (areaInvalid): When the area is invalid.
        400 (valueError): Validation errors.
        500: Database-related exceptions (InterfaceError, ProgrammingError, DatabaseError, OperationalError).
    """
    data = request.json
    db = get_db()
    cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    try:
        validate_json_schema(data, schema)
        validate_spot_query(data)
    except (exceptions.ValidationError, ValueError) as e:
        print(e)
        return jsonify({"error": str(e)}), 400

    try:
        cleaned_spot_query = clean_spot_query(data)
        set_area(cleaned_spot_query)
        query = constructor.construct_query_from_graph(cleaned_spot_query)

        return query.as_string(cursor)

    except AreaInvalidError as e:
        response = {
            "status": "error",
            "errorType": "areaInvalid",
            "message": str(e),
        }

        return jsonify(response), 422

    except ValueError as e:
        response = {
            "status": "error",
            "errorType": "valueError",
            "message": str(e),
        }

        return jsonify(response), 400

    except (InterfaceError, ProgrammingError, DatabaseError, OperationalError) as e:
        response = {
            "status": "error",
            "errorType": str(e),
        }

        return jsonify(response), 500

@app.route("/run-spot-query", methods=["POST"])
def run_spot_query_route():
    """
    Execute a validated spot query and return results as GeoJSON along with metadata.

    Process:
        1) Validate input JSON (schema + custom checks).
        2) Clean query, set area, and verify area surface (via `check_area_surface`).
        3) Construct the SQL query from the graph.
        4) Apply statement timeout from TIMEOUT env var and execute the query.
        5) Transform rows to GeoJSON; compute set stats; include timing checkpoints.

    Returns:
        (flask.Response, int): 200 with payload:
            {
              "results": <GeoJSON FeatureCollection>,
              "query": <SQL string> (only in development),
              "area": <area value> (if available),
              "sets": {"distinct_sets": [...], "stats": {...}},
              "timing": [...],
              "status": "success"
            }
        Error responses:
            422 areaInvalid,
            408 queryTimeout (QueryCanceledError),
            400 valueError,
            500 database exceptions.
    """
    timer = Timer()
    data = request.json
    db = get_db()

    try:
        validate_json_schema(data, schema)
        validate_spot_query(data)

    except (exceptions.ValidationError, ValueError) as e:
        return (
            jsonify(
                {"status": "error", "errorType": "spot_queryInvalid", "message": str(e)}
            ),
            400,
        )

    try:
        cleaned_spot_query = clean_spot_query(data)
        set_area(cleaned_spot_query)
        timer.add_checkpoint("area_setting")
        check_area_surface(g.db)

        query = constructor.construct_query_from_graph(cleaned_spot_query)

        timer.add_checkpoint("query_construction")

        cursor = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
        timeout = int(os.getenv("TIMEOUT", 20000))
        cursor.execute("SET statement_timeout = %s", (timeout,))
        db.commit()
        cursor.execute(query)
        timer.add_checkpoint("query_execution")

        # Fetch all results as a list of dictionaries
        results = [dict(record) for record in cursor]

        # spots = get_spots(results)
        geojson = results_to_geojson(results)
        timer.add_checkpoint("results_transformation_to_geojson")

        distinct_set_names = list({result["set_name"] for result in results})
        set_name_counts = dict(Counter(result["set_name"] for result in results))
        area_value = getattr(g, "area", None)

        response = {
            "results": geojson,
            **(
                {"query": query.as_string(cursor)}
                if environment == "development"
                else {}
            ),
            **({"area": area_value} if area_value is not None else {}),
            "sets": {"distinct_sets": distinct_set_names, "stats": set_name_counts},
            # "spots": spots,
            "timing": timer.get_all_checkpoints(),
            "status": "success",
        }

        return jsonify(response), 200

    except AreaInvalidError as e:
        timer.add_checkpoint("error")
        response = {
            "status": "error",
            "errorType": "areaInvalid",
            "message": str(e),
            "timing": timer.get_all_checkpoints(),
        }

        return jsonify(response), 422

    except QueryCanceledError:
        timer.add_checkpoint("timeout")
        response = {
            "status": "error",
            "errorType": "queryTimeout",
            "timing": timer.get_all_checkpoints(),
        }

        return jsonify(response), 408

    except ValueError as e:
        timer.add_checkpoint("error")
        response = {
            "status": "error",
            "errorType": "valueError",
            "message": str(e),
            "timing": timer.get_all_checkpoints(),
        }

        return jsonify(response), 400

    except (InterfaceError, ProgrammingError, DatabaseError, OperationalError) as e:
        timer.add_checkpoint("error")
        response = {
            "status": "error",
            "errorType": str(e),
            "timing": timer.get_all_checkpoints(),
        }

        return jsonify(response), 500

if __name__ == "__main__":
    app.run(debug=True)
