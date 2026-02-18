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


def construct_search_area_cte(type):
    """
    Constructs a SQL Common Table Expression (CTE) that defines a spatial
    search area, either as a bounding box or a GeoJSON geometry.

    Args:
        type (str): Type of area definition. Supported values:
            - "bbox": Uses coordinates from `g.area["bbox"]` to create an envelope.
            - "area": Uses a GeoJSON geometry from `g.area["geometry"]`.

    Returns:
        psycopg2.sql.Composed: A SQL CTE defining the envelope geometry.

    Raises:
        AreaInvalidError: If there is a failure constructing the geometry or CTE.

    Notes:
        - Uses the current UTM projection from `flask.g.utm`.
        - The output CTE is named "envelope" and contains a single geometry column.
    """
    # Handle bounding box type
    try:
        if type == "bbox":
            geometry = sql.SQL(
                "ST_MakeEnvelope({xmin}, {ymin}, {xmax}, {ymax}, {utm})"
            ).format(
                utm=sql.Literal(g.utm),
                xmin=sql.Literal(g.area["bbox"][0]),
                ymin=sql.Literal(g.area["bbox"][1]),
                xmax=sql.Literal(g.area["bbox"][2]),
                ymax=sql.Literal(g.area["bbox"][3]),
            )

        # Handle geojson area type
        elif type == "area":
            geometry = sql.SQL("ST_SIMPLIFY(ST_GeomFromGeoJSON({searchAreaGeometry}), 0.001)").format(
                searchAreaGeometry=sql.Literal(g.area["geometry"]),
            )

        # Construct the CTE (Common Table Expression) using the generated geometry
        cte = sql.SQL("{envelope} AS (SELECT {geometry} AS geom)").format(
            envelope=sql.Identifier("envelope"), geometry=geometry
        )

        return cte
    except Exception as e:
        raise AreaInvalidError(e)
