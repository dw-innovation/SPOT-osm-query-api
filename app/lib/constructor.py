from .ctes.construct import construct_ctes
from .construct_relations import construct_relations
from psycopg2 import sql
from flask import g

"""
Build a complete SQL query (WITH CTEs + JOINed relations) from a graph spec.

This module orchestrates two steps:
1) `construct_ctes(spot_query)` generates Common Table Expressions (CTEs) for all nodes.
2) `construct_relations(spot_query)` generates the CTE that relates those nodes and
   the main SELECT.

The result is a single `psycopg2.sql` composed query ready for execution.
"""

def construct_query_from_graph(spot_query):
    """Compose a full SQL query from a graph-like `spot_query`.

    This function delegates to:
      - `construct_ctes(spot_query)` to build node CTEs, and
      - `construct_relations(spot_query)` to build the CTE with the spatial joins
        and the main SELECT.

    The parts are combined into a single `WITH ... SELECT ...` query using
    `psycopg2.sql` objects to ensure identifier/literal safety.

    Args:
      spot_query (dict): A graph specification that includes:
        - nodes: list of {"id": <int|str>, "name": <str>}
        - edges: list of {"source": <node_id>, "target": <node_id>, "type": <str>, ...}
        The exact schema must satisfy the expectations of `construct_ctes` and
        `construct_relations`.

    Returns:
      psycopg2.sql.Composed | None: The composed SQL query if successful; otherwise
      `None` if an exception is raised during construction (the error is printed).

    Notes:
      - Exceptions are caught and printed, and the function returns `None`. If you
        prefer failures to propagate to callers, remove the try/except.
      - The import `flask.g` is present if you intend to use request-scoped context,
        but it is not referenced in this function.
    """
    try:
        # Construct the node CTEs based on the intermediate representation
        ctes = construct_ctes(spot_query)

        # Construct the relations (JOINs) based on the intermediate representation
        relation_ctes, relations = construct_relations(spot_query)

        # Combine all CTEs with the SQL WITH clause
        combined_ctes = sql.SQL("WITH ") + sql.SQL(", ").join(ctes + relation_ctes)

        # Combine CTEs and relations to form the final query
        final_query = sql.SQL(" ").join([combined_ctes, relations])

        # Return the final SQL query
        return final_query

    except Exception as e:
        print(f"An error occurred in constructor.py: {e}")
        return None
