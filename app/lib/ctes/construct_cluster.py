import os
from flask import g
from ..utils import distance_to_meters, eps_to_h3_resolution
from .construct_where_clause import construct_cte_where_clause
from psycopg2 import sql


def construct_cluster_cte(node):
    try:
        eps = node.get("maxDistance", "50")
        eps_in_meters = float(distance_to_meters(eps))
        min_points = node.get("minPoints", 2)
        set_id = node.get("id", "id")
        set_name = node.get("name", "name")
        cluster_name = f"cluster_{set_id}_{set_name}".replace(" ", "_")
        h3_resolution = eps_to_h3_resolution(eps_in_meters)

        print(g.utm, eps_in_meters, min_points, set_id, set_name, f"h3_res={h3_resolution}")
        filters = construct_cte_where_clause(node.get("filters", []))

        query = sql.SQL(
            """WITH h3_filtered AS (
                SELECT
                    h3_lat_lng_to_cell(ST_Y(geom), ST_X(geom), {h3_resolution}) AS h3_cell,
                    node_id,
                    geom,
                    ST_Transform(geom, {utm}) AS transformed_geom,
                    tags,
                    primitive_type
                FROM {table_view}
                WHERE
                    {filters}
            ),
            clusters AS (
                SELECT
                    ST_ClusterDBSCAN(transformed_geom, eps := {eps}, minpoints := {min_pts})
                        OVER (PARTITION BY h3_cell) AS cluster_id,
                    h3_cell,
                    node_id,
                    geom,
                    transformed_geom,
                    tags,
                    primitive_type
                FROM h3_filtered
            )
            SELECT
                'cluster_' || {cluster_name} || h3_cell::text || '_' || cluster_id::text AS id,
                ST_Centroid(ST_Collect(geom)) AS geom,
                ARRAY_AGG(primitive_type || '/' || node_id::text) AS osm_ids,
                {set_id} AS set_id,
                {set_name} AS set_name,
                ST_Centroid(ST_Collect(transformed_geom)) AS transformed_geom,
                tags,
                primitive_type
            FROM clusters
            WHERE cluster_id IS NOT NULL
            GROUP BY h3_cell, cluster_id, tags, primitive_type"""
        ).format(
            utm=sql.Literal(g.utm),
            eps=sql.Literal(eps_in_meters),
            min_pts=sql.Literal(min_points),
            cluster_name=sql.Literal(cluster_name),
            h3_resolution=sql.Literal(h3_resolution),
            table_view=sql.Identifier(os.getenv("TABLE_VIEW")),
            filters=filters,
            set_id=sql.Literal(set_id),
            set_name=sql.Literal(set_name),
        )

        cte = sql.SQL("{set_id} AS ({q})").format(
            set_id=sql.Identifier(str(set_id)),
            q=query,
        )

        return cte

    except Exception as e:
        print(f"An error occurred in cluster.py: {e}")
        return None
