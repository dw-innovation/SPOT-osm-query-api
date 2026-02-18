from psycopg2 import sql
import os
from flask import g
from .construct_where_clause import construct_cte_where_clause


def construct_nwr_cte(node):
    """
    Constructs a SQL Common Table Expression (CTE) to select and transform
    Node/Way/Relation (NWR) data from an OSM-derived table based on given filters.

    This CTE:
      - Applies spatial transformation to geometries.
      - Filters data using provided tag-based filters.
      - Outputs metadata such as set ID, name, geometry, and tags.

    Args:
        node (dict): A dictionary containing node configuration. Expected keys:
            - "id" (str or int): Identifier for the resulting dataset (used as CTE name and set_id).
            - "name" (str): Human-readable name of the set.
            - "filters" (list): A list of filter clauses used in the WHERE condition.

    Returns:
        psycopg2.sql.Composed: A SQL CTE expression for retrieving and filtering NWR features.

    Notes:
        - Uses the UTM projection zone from `flask.g.utm` to transform geometries.
        - Reads the target table name from the `TABLE_VIEW` environment variable.
    """
    set_id = node.get("id", 0)
    set_name = node.get("name", "name")
    filters = construct_cte_where_clause(node.get("filters", []))

    query = sql.SQL(
        """
        SELECT 
            {set_id} AS set_id,
            ST_Transform(geom, {utm}) AS transformed_geom,
            geom,
            ARRAY[primitive_type || '/' || node_id] AS osm_ids,
            {set_id} AS set_id,
            {set_name} AS set_name,
            tags,
            primitive_type
        FROM
            {table_view}
        WHERE
            {filters}
        """
    ).format(
        set_id=sql.Literal(str(set_id)),
        utm=sql.Literal(g.utm),
        set_name=sql.Literal(set_name),
        table_view=sql.Identifier(os.getenv("TABLE_VIEW")),
        filters=filters
    )

    cte = sql.SQL("{set_id} AS ({query})").format(
        set_id=sql.Identifier(str(set_id)),
        query=query
    )

    return cte
