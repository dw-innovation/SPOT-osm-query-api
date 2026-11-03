import json
import re
from flask import g
import requests
from shapely import wkb
from shapely.wkb import loads as wkb_loads
from shapely.geometry import (
    MultiPolygon,
    Polygon,
    MultiPoint,
    LineString,
    Point,
    mapping,
    shape,
)
from math import cos, radians
from psycopg2 import sql
from collections import defaultdict
import os


from .ctes.construct_search_area import AreaInvalidError

"""
Geospatial utilities for Spot:
- Geometry conversions between PostGIS (WKB) and GeoJSON.
- Area handling (bbox/geometry), UTM zone detection, and size validation.
- Result shaping (GeoJSON FeatureCollection and "spots" aggregation).
- Graph spec validation and canonicalization (sorting/normalizing).

The functions here are designed to work with Flask's request context (`flask.g`)
and PostGIS-enabled queries (via `psycopg2.sql`).
"""

def geom_bin_to_geojson(geom_bin):
    """Convert a PostGIS hex‑WKB geometry to a GeoJSON geometry mapping.

    Args:
        geom_bin (str | bytes): Hex-encoded WKB string (or bytes) returned by PostGIS.

    Returns:
        dict: A GeoJSON geometry mapping (as produced by `shapely.geometry.mapping`).

    Raises:
        shapely.errors.ReadingError: If the geometry cannot be parsed.
    """
    geom_wkt = wkb.loads(geom_bin, hex=True)  # Convert binary geometry to WKT
    return mapping(geom_wkt)  # Convert WKT to GeoJSON


def add_center_to_geojson(geojson_feature):
    """Compute and attach the centroid of a GeoJSON feature as `properties.center`.

    Args:
        geojson_feature (dict): A GeoJSON Feature with a valid `geometry`.

    Side Effects:
        Mutates `geojson_feature["properties"]["center"]` to a GeoJSON Point mapping.

    Raises:
        ValueError: If `geometry` is missing or invalid.
    """
    geometry_shape = shape(geojson_feature["geometry"])
    centroid = geometry_shape.centroid
    center_point = Point(centroid.x, centroid.y).__geo_interface__
    geojson_feature["properties"]["center"] = center_point


def results_to_geojson(results):
    """Convert a sequence of DB result rows into a GeoJSON FeatureCollection.

    Each row is expected to contain a PostGIS geometry (under key `"geom"`) and
    any additional attributes that will be placed under `properties`. The geometry
    is converted from hex‑WKB to a GeoJSON geometry. A centroid is added to each
    feature under `properties.center`.

    Args:
        results (list[dict]): Iterable of row dicts where `"geom"` is a hex‑WKB.

    Returns:
        dict: A GeoJSON FeatureCollection mapping.

    Notes:
        The `"geom"` key is removed from each row before it is assigned to
        `properties`.
    """
    features = []

    for result in results:
        geom_geojson = geom_bin_to_geojson(
            result["geom"]
        )  # Convert geometry to GeoJSON

        # Remove 'geom' key from the result before assigning to properties since it's not needed in the properties
        del result["geom"]

        feature = {
            "type": "Feature",
            "geometry": geom_geojson,
            "properties": result,
        }

        add_center_to_geojson(feature)  # Add center point to the feature

        features.append(feature)

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    return geojson


def distance_to_meters(distance_str: str) -> str:
    """Normalize a distance string with units into meters.

    Accepts values like `"50m"`, `"0.2km"`, `"3 miles"`, `"12ft"`, `"100"`.
    If no unit is provided, the value is interpreted as meters.

    Args:
        distance_str (str): Value and optional unit.

    Returns:
        str: The numeric distance in meters (stringified).

    Raises:
        ValueError: If the string format is invalid or uses an unknown unit.
    """
    # Conversion rates for different units
    conversion_rates = {
        "m": 1,
        "meters": 1,
        "meter": 1,
        "metres": 1,
        "km": 1000,
        "kilometer": 1000,
        "kilometers": 1000,
        "ft": 0.3048,
        "foot": 0.3048,
        "mile": 1609.34,
        "miles": 1609.34,
        "mi": 1609.34,
        "yd": 0.9144,
        "yard": 0.9144,
        "yards": 0.9144,
        "in": 0.0254,
        "inch": 0.0254,
        "inches": 0.0254,
        "cm": 0.01,
        "centimeter": 0.01,
        "centimeters": 0.01,
        "mm": 0.001,
        "millimeter": 0.001,
        "millimeters": 0.001,
    }

    # Use regex to extract value and unit
    match = re.match(r"(?P<value>[\d.]+)\s?(?P<unit>[a-zA-Z]*)", distance_str)

    if not match:
        raise ValueError(f"Invalid format: {distance_str}")

    value_str = match.group("value")
    unit = match.group("unit").lower()

    # If there's no unit, return the numeric value as a string
    if not unit:
        return str(float(value_str))

    # Checking if unit is valid
    if unit not in conversion_rates:
        raise ValueError(f"Unknown distance unit: {unit}")

    value = float(value_str)

    ## Converting to meters
    distance_meters = str(value * conversion_rates[unit])

    return distance_meters


def get_utm(longitude: float, latitude: float) -> int:
    """Determine the EPSG code for the UTM zone of a lon/lat location.

    Args:
        longitude (float): Longitude in degrees, must be in [-180, 180].
        latitude (float): Latitude in degrees, must be in [-80, 84] for UTM.

    Returns:
        int: EPSG code for the matching UTM zone (326xx for N, 327xx for S).

    Raises:
        ValueError: If latitude/longitude are outside supported ranges.
    """
    if -180 <= longitude <= 180:
        zone_number = int((longitude + 180) / 6) + 1
        if -80 <= latitude < 0:
            return 32700 + zone_number  # Southern Hemisphere
        elif 0 <= latitude <= 84:
            return 32600 + zone_number  # Northern Hemisphere
        else:
            raise ValueError(
                "Latitude out of range. UTM only supports between 80°S and 84°N."
            )
    else:
        raise ValueError("Longitude out of range.")


def set_area(data: str) -> None:
    """Parse the incoming area spec (bbox or polygon) and populate `g.area` & `g.utm`.

    Supports:
      - `{"area": {"type": "bbox", "bbox": [min_lon, min_lat, max_lon, max_lat]}}`
      - `{"area": {"type": "area", "geometry": <GeoJSON Polygon/MultiPolygon>}}`

    For a bbox, the center is computed from the bounds; for a polygon, the centroid
    is computed using Shapely. In both cases the UTM EPSG is determined from the
    center/centroid and stored in `g.utm`.

    Args:
        data (dict): Area payload as described above.

    Raises:
        AreaInvalidError: If anything goes wrong while parsing or computing.
    """
    try:
        type = data["area"]["type"]

        if not hasattr(g, "area"):
            g.area = {}

        if type == "bbox":
            g.area["type"] = "bbox"
            g.area["bbox"] = data["area"]["bbox"]
            min_lon, min_lat, max_lon, max_lat = data["area"]["bbox"]
            center_lon = (min_lon + max_lon) / 2
            center_lat = (min_lat + max_lat) / 2
            g.area["center"] = [center_lon, center_lat]
            g.utm = get_utm(center_lon, center_lat)

        elif type == "area":
            g.area["type"] = "area"
            geometry = data["area"]["geometry"]
            g.area["geometry"] = json.dumps(geometry).replace("\\\"", "\"")

            polygon = shape(geometry)
            centroid = polygon.centroid
            g.area["center"] = [centroid.x, centroid.y]
            g.utm = get_utm(centroid.x, centroid.y)
    except Exception as e:
        print(f"An error occurred in area.py: {e}")
        raise AreaInvalidError(e)


def check_area_surface(db):
    """Validate that the selected area does not exceed the configured size limit.

    Uses the current `g.area` and `g.utm` to compute the area (in m²) and compares
    it to `MAX_AREA` (in km²) taken from the environment (default: 5000).

    Args:
        db (psycopg2.extensions.connection): Open database connection.

    Raises:
        AreaInvalidError: If the area is larger than the allowed maximum.
    """
    area = calculate_area_size(db)

    area_sqkm = area / 1e6

    max_area = int(os.getenv("MAX_AREA", 5000))

    if area_sqkm > max_area:
        raise AreaInvalidError("areaExceedsLimit")


def calculate_area_size(db) -> float:
    """Compute the surface of the active area in square meters.

    Uses PostGIS to compute the area after transforming to the area's UTM EPSG.

    Args:
        db (psycopg2.extensions.connection): Open database connection.

    Returns:
        float: Area in square meters.

    Raises:
        KeyError: If `g.area` is not set or missing required keys.
        psycopg2.Error: If the SQL execution fails.
    """
    cursor = db.cursor()

    if g.area["type"] == "bbox":
        bbox = g.area["bbox"]
        query = sql.SQL(
            "SELECT ST_Area(ST_Transform(ST_MakeEnvelope({}, {}, {}, {}, 4326), {}))"
        ).format(
            sql.Literal(bbox[0]),
            sql.Literal(bbox[1]),
            sql.Literal(bbox[2]),
            sql.Literal(bbox[3]),
            sql.Literal(g.utm),
        )
    elif g.area["type"] == "area":
        query = sql.SQL(
            "SELECT ST_Area(ST_Transform(ST_GeomFromGeoJSON({}), {}))"
        ).format(sql.Literal(g.area["geometry"]), sql.Literal(g.utm))

    cursor.execute(query)

    return cursor.fetchone()[0]


def get_spots(results):
    """Aggregate raw geometry rows into "spots" grouped by primary OSM ID.

    For each record, geometries are decoded from WKB and their coordinates
    are accumulated per primary OSM ID. A small lat/lon buffer (~100 m)
    is applied to build a bbox around all coordinates for that spot.

    Args:
        results (list[dict]): Rows expected to include:
            - "primary_osm_ids" (iterable[int|str]): One or more primary OSM IDs.
            - "osm_ids" (int|str): The row's OSM ID.
            - "geom" (bytes): WKB geometry (NOT hex-encoded).
            - "tags" (dict | None): Optional tag mapping.

    Returns:
        list[dict]: Each item has:
            - "bbox": [minx, miny, maxx, maxy]
            - "id": primary OSM ID
            - "tags": tags (from the row where `osm_ids == primary_osm_id`, if any)
            - "nodes": unique list of member OSM IDs

    Notes:
        - Supports Point, LineString, Polygon, and MultiPolygon. Others are ignored.
        - Buffer uses a crude degrees approximation (sufficient for small extents).
    """
    grouped = defaultdict(lambda: {"coords": [], "tags": None, "nodes": []})
    spots = []

    for record in results:
        primary_osm_id = record["primary_osm_ids"]  # Assuming this is an array

        for primary_osm_id in primary_osm_id:
            if record["osm_ids"] == primary_osm_id:
                grouped[primary_osm_id]["tags"] = record["tags"]

            grouped[primary_osm_id]["nodes"].append(record["osm_ids"])

            geom = wkb_loads(record["geom"], hex=False)
            coords = []

            if isinstance(geom, Point):
                coords = [geom.coords[0]]
            elif isinstance(geom, LineString):
                coords = list(geom.coords)
            elif isinstance(geom, Polygon):
                coords = list(geom.exterior.coords)
            elif isinstance(geom, MultiPolygon):
                for (
                    polygon
                ) in (
                    geom.geoms
                ):  # Corrected iteration over each Polygon in a MultiPolygon
                    coords.extend(list(polygon.exterior.coords))

            else:
                pass

            grouped[primary_osm_id]["coords"].extend(coords)

    buffer_meters = 100
    buffer_lat = buffer_meters / 111000.0

    for primary_osm_id, data in grouped.items():
        multi_point = MultiPoint(data["coords"])
        minx, miny, maxx, maxy = multi_point.bounds
        buffer_lon = buffer_meters / (111000.0 * cos(radians(miny)))

        minx -= buffer_lon
        miny -= buffer_lat
        maxx += buffer_lon
        maxy += buffer_lat

        bbox = [minx, miny, maxx, maxy]
        tags = data["tags"]
        nodes = list(set(data["nodes"]))  # Remove duplicates """

        spots.append({"bbox": bbox, "id": primary_osm_id, "tags": tags, "nodes": nodes})

    return spots


def validate_spot_query(spot_query):
    """Validate the graph specification for a Spot query.

    Ensures the presence and uniqueness of nodes, well-formed edges (no self‑loops,
    no duplicates or inverted duplicates), and that each node has at least one valid
    filter (directly or nested in `and`/`or`).

    Args:
        spot_query (dict): Must include:
            - "nodes": list of {"id": ..., "name": ..., "filters": ...}
            - "edges": optional list of {"source": id, "target": id, ...}

    Raises:
        ValueError: With one of these codes (string):
            - "missingFilterInNode"
            - "missingNodes"
            - "duplicateNodeIds"
            - "duplicateNodeNames"
            - "duplicateOrInvertedEdge"
            - "missingEdgeSourceOrTarget"
            - "selfReferencingEdge"
            - "edgeSourceOrTargetNotInNodes"
    """
    edges, nodes = spot_query.get("edges"), spot_query.get("nodes")

    for node in nodes:
        filters = node.get("filters")
        if not validate_has_filter(filters):
            raise ValueError(f"missingFilterInNode")

    if nodes is None:
        raise ValueError("missingNodes")

    node_ids = {node["id"] for node in nodes}
    node_names = {node["name"] for node in nodes}
    seen_edges = set()

    if len(node_ids) != len(nodes):
        raise ValueError("duplicateNodeIds")

    if len(node_names) != len(nodes):
        raise ValueError("duplicateNodeNames")

    if edges is None:
        return

    for edge in edges:
        source, target = edge.get("source"), edge.get("target")

        edge_tuple = (min(source, target), max(source, target))
        if edge_tuple in seen_edges:
            raise ValueError("duplicateOrInvertedEdge")

        seen_edges.add(edge_tuple)

        if None in [source, target]:
            raise ValueError("missingEdgeSourceOrTarget")

        if source == target:
            raise ValueError("selfReferencingEdge")

        if not {source, target}.issubset(node_ids):
            raise ValueError("edgeSourceOrTargetNotInNodes")


def validate_has_filter(filter_nodes):
    """Check whether a filter tree contains at least one `{"key": ...}` predicate.

    The filter schema is expected to be a list of nodes where each node either:
    - Has a `"key"` (a terminal predicate), or
    - Has `"and"` / `"or"` with nested lists following the same schema.

    Args:
        filter_nodes (list | None): Filter nodes list or `None`.

    Returns:
        bool: True if at least one terminal predicate exists; False otherwise.
    """
    if not filter_nodes:
        return False

    for filter_node in filter_nodes:
        if "key" in filter_node:
            return True
        elif "and" in filter_node:
            if validate_has_filter(filter_node["and"]):
                return True
        elif "or" in filter_node:
            if validate_has_filter(filter_node["or"]):
                return True
    return False


def clean_spot_query(spot_query):
    """Canonicalize a Spot graph spec by sorting nodes/edges and orienting edges.

    - Nodes are sorted by `"id"`.
    - For each edge, if `source >= target`, the endpoints are swapped so that
      `source < target` (normalization helps detect duplicates).
    - Edges are then sorted by `(source, target)`.

    Args:
        spot_query (dict): A graph specification with `nodes` and `edges`.

    Returns:
        dict: The same dict instance, mutated in place and returned for convenience.
    """
    nodes, edges = spot_query.get("nodes", []), spot_query.get("edges", [])

    # Sort nodes by 'id'
    sorted_nodes = sorted(nodes, key=lambda x: x["id"])
    spot_query["nodes"] = sorted_nodes

    # Invert 'source' and 'target' where 'target' is not greater than 'source' and sort edges
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source >= target:
            edge["source"], edge["target"] = target, source

    sorted_edges = sorted(edges, key=lambda x: (x["source"], x["target"]))
    spot_query["edges"] = sorted_edges

    return spot_query
