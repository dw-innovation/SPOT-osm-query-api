from psycopg2 import sql
from flask import g


class AreaInvalidError(Exception):
    """
    Custom exception raised when the construction of a spatial area fails.

    This may occur due to:
      - Invalid or missing bounding box coordinates.
      - Malformed or missing GeoJSON geometry.
      - Any other issue during SQL query construction related to the area.
    """
    pass


def construct_search_area_geometry():
    """
    Builds the SQL expression for the search area of the current request,
    either a bounding box or a GeoJSON geometry, in WGS84 (SRID 4326) like the
    geometries it is compared against.

    The expression contains only literals, so PostgreSQL folds it into a
    constant while planning.

    Returns:
        psycopg2.sql.Composed: A SQL expression evaluating to the area geometry.

    Raises:
        AreaInvalidError: If the area type is unknown or the area is malformed.

    Notes:
        - Reads the area from `flask.g.area`:
            - "bbox": coordinates from `g.area["bbox"]`.
            - "area": a GeoJSON geometry from `g.area["geometry"]`.
    """
    try:
        area_type = g.area["type"]

        # Handle bounding box type
        if area_type == "bbox":
            return sql.SQL(
                "ST_MakeEnvelope({xmin}, {ymin}, {xmax}, {ymax}, 4326)"
            ).format(
                xmin=sql.Literal(g.area["bbox"][0]),
                ymin=sql.Literal(g.area["bbox"][1]),
                xmax=sql.Literal(g.area["bbox"][2]),
                ymax=sql.Literal(g.area["bbox"][3]),
            )

        # Handle geojson area type
        if area_type == "area":
            return sql.SQL(
                "ST_SetSRID(ST_Simplify(ST_GeomFromGeoJSON({searchAreaGeometry}), 0.001), 4326)"
            ).format(
                searchAreaGeometry=sql.Literal(g.area["geometry"]),
            )
    except Exception as e:
        raise AreaInvalidError(e)

    raise AreaInvalidError(f"Unsupported area type: {area_type}")


def construct_search_area_filter():
    """
    Builds the SQL condition that restricts a table scan to the search area.

    The area is written into the condition as a constant instead of being read
    from a CTE (`geom && (SELECT geom FROM envelope)`). With a subquery the
    planner does not know the area while planning and falls back to a default
    selectivity; with a constant it can estimate from the column statistics how
    many rows the area covers and pick the plan accordingly.

    Returns:
        psycopg2.sql.Composed: A SQL condition on the `geom` column.

    Raises:
        AreaInvalidError: If the area type is unknown or the area is malformed.
    """
    return sql.SQL("geom && {area}").format(area=construct_search_area_geometry())
