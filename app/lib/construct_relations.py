from collections import defaultdict
from .utils import distance_to_meters, eps_to_h3_resolution
import os
from psycopg2 import sql

def get_max_nodes_per_set():
    """
    Maximum number of distinct result rows returned per set (subquery),
    configurable via the MAX_NODES_PER_SET environment variable (default: 500).
    One extra row is fetched per set so callers can detect that a set was
    truncated. Read lazily so values loaded from .env via load_dotenv() are
    picked up regardless of import order.
    """
    return int(os.getenv("MAX_NODES_PER_SET", 500))


def construct_relations(spot_query):
    """Build a composed SQL query from a graph of spatial relations.

    This function turns a lightweight graph specification into a single
    `psycopg2.sql` composed query. Nodes represent source tables (by numeric ID),
    and edges describe spatial relations between those tables. Supported
    relations:

    - `"distance"`: Uses `ST_DWithin(source.transformed_geom, target.transformed_geom, meters)`
      where the distance is parsed and normalized to meters via `distance_to_meters`.
    - `"contains"`: Uses `ST_Intersects(source.transformed_geom, target.transformed_geom)`.

    The output query:
    - Produces a UNION of per-node SELECTs.
    - Includes `set_name`, `osm_ids`, `geom`, `tags`, `primitive_type`.
    - Adds `primary_osm_id` when a node participates in a relation (derived from
      the first node in the provided `nodes` list), otherwise `NULL`.
    - Groups final results to deduplicate identical rows.

    Args:
      spot_query (dict):
        A graph-like specification with the following shape:
        ```
        {
          "nodes": [
            {"id": <int|str>, "name": <str>}, ...
          ],
          "edges": [
            {
              "source": <node_id>,
              "target": <node_id>,
              "type": "distance" | "contains",
              # required when type == "distance"; examples: "50m", "0.2km"
              "value": <str|number>
            },
            ...
          ]
        }
        ```
        Notes:
        - Each `nodes[i]["id"]` is the actual table identifier used in FROM/JOIN
          clauses (cast to text).
        - Each `nodes[i]["name"]` becomes the SQL table alias for that node.

    Returns:
      psycopg2.sql.Composed:
        A fully composed and parameter-safe SQL object that can be passed to
        `cursor.execute(final_query)`.

    Raises:
      ValueError: If an edge references the same source and target node
        (`"selfReferencingEdge"`).

    Examples:
      Basic usage:
      >> final_query = construct_relations({
      ...   "nodes": [
      ...     {"id": 123, "name": "parks"},
      ...     {"id": 456, "name": "schools"}
      ...   ],
      ...   "edges": [
      ...     {"source": 123, "target": 456, "type": "distance", "value": "500m"}
      ...   ]
      ... })
      >> # cursor.execute(final_query)

    Implementation details:
      - Uses `psycopg2.sql.Identifier` and `psycopg2.sql.Literal` to avoid SQL injection.
      - Chains multiple join conditions per target with `AND`.
      - Only nodes that appear in at least one edge are joined; isolated nodes are
        still returned via standalone SELECTs.

    Complexity:
      O(N + E) to build the query components, where N is the number of nodes and
      E is the number of edges.
    """
    edges = spot_query.get(
        "edges", []
    )  # Get edges from input map relation, set to empty list if not found
    nodes = spot_query.get("nodes", None)  # Get nodes from input map relation

    # Create a set to keep track of all nodes that are either source or target
    referenced_nodes = set()

    # Creating a mapping from node IDs to node names
    id_to_name = {node["id"]: node["name"] for node in nodes}

    # Using defaultdict to manage the join conditions for each target node
    join_conditions = defaultdict(list)

    # Loop through each edge in the input graph
    for edge in edges:
        source_id = edge["source"]
        target_id = edge["target"]

        if source_id == target_id:
            raise ValueError("selfReferencingEdge")

        # Get source and target node names

        source_name = id_to_name[source_id]
        target_name = id_to_name[target_id]

        referenced_nodes.add(id_to_name[edge["source"]])
        referenced_nodes.add(id_to_name[edge["target"]])

        # Get the type of relation between nodes
        type = edge["type"]

        # Process 'distance' type
        if type == "distance":
            distance = distance_to_meters(edge["value"])  # Convert distance to meters
            h3_resolution = eps_to_h3_resolution(float(distance))

            # H3 neighbor pre-filter narrows the cross-join to candidate pairs
            # whose hex cells are adjacent (k=1 ring = cell itself + 6 neighbors).
            # ST_DWithin then confirms exact distance on that reduced candidate set.
            condition = sql.SQL(
                "h3_lat_lng_to_cell(ST_Y({source_alias}.geom), ST_X({source_alias}.geom), {h3_res})"
                " = ANY(h3_grid_disk("
                "h3_lat_lng_to_cell(ST_Y({target_alias}.geom), ST_X({target_alias}.geom), {h3_res})"
                ", 1))"
                " AND ST_DWithin({source_alias}.transformed_geom, {target_alias}.transformed_geom, {distance})"
            ).format(
                source_alias=sql.Identifier(source_name),
                target_alias=sql.Identifier(target_name),
                h3_res=sql.Literal(h3_resolution),
                distance=sql.Literal(distance),
            )

        # Process 'contains' type
        elif type == "contains":
            # Formulate SQL join condition for containment
            condition = sql.SQL(
                "ST_Intersects({source_alias}.transformed_geom, {target_alias}.transformed_geom)"
            ).format(
                source_alias=sql.Identifier(source_name),
                target_alias=sql.Identifier(target_name),
            )

        # Add condition to corresponding target node
        join_conditions[target_name].append(condition)

    # Process each target node and its conditions
    all_joins = []
    for target_name, conditions in join_conditions.items():
        # Chain multiple conditions with AND
        chained_conditions = sql.SQL(" AND ").join(conditions)

        # Formulate SQL JOIN clause
        join_clause = sql.SQL("JOIN {target} {target_alias} ON {conditions}").format(
            target=sql.Identifier(
                str(
                    next(
                        edge["target"]
                        for edge in edges
                        if id_to_name[edge["target"]] == target_name
                    )
                )
            ),
            target_alias=sql.Identifier(target_name),
            conditions=chained_conditions,
        )
        all_joins.append(join_clause)

    # Combine all JOIN clauses
    all_joins = sql.SQL(" ").join(all_joins)

    # Generate final SQL queries
    final_queries = []

    for node in nodes:
        node_name = node["name"]
        first_id = sql.Identifier(str(nodes[0]["id"]))
        first_name = sql.Identifier(str(nodes[0]["name"]))

        if node_name in referenced_nodes:
            # Handle nodes that are referenced by at least one edge and construct SQL query with JOINs
            query_part = sql.SQL(
                """
                    SELECT 
                        {type} AS set_name, 
                        {name_alias}.osm_ids, 
                        {name_alias}.geom, 
                        {name_alias}.tags, 
                        {name_alias}.primitive_type,
                        {first_name_alias}.osm_ids AS primary_osm_id
                    FROM {first_id} {first_name_alias}
                    {joins}"""
            ).format(
                type=sql.Literal(node_name),
                name_alias=sql.Identifier(node_name),
                first_id=first_id,
                first_name_alias=first_name,
                joins=all_joins,
            )

            final_queries.append(query_part)
        else:
            # Handle isolated nodes that are not referenced by any edge
            isolated_query = sql.SQL(
                """
                SELECT 
                    {type} AS set_name, 
                    {name_alias}.osm_ids, 
                    {name_alias}.geom, 
                    {name_alias}.tags,
                    {name_alias}.primitive_type,
                    NULL AS primary_osm_id
                FROM {id} {name_alias}
                """
            ).format(
                type=sql.Literal(node_name),
                name_alias=sql.Identifier(node_name),
                id=sql.Identifier(str(node["id"])),
            )
            final_queries.append(isolated_query)

    # Combine all SQL queries using UNION ALL
    union = sql.SQL(" UNION ALL ").join(final_queries)

    final_query = sql.SQL(
        """
            SELECT
                deduped.set_name,
                deduped.osm_ids,
                deduped.geom,
                deduped.tags,
                deduped.primitive_type
            FROM (
                SELECT
                    subquery.set_name,
                    subquery.osm_ids,
                    subquery.geom,
                    subquery.tags,
                    subquery.primitive_type,
                    ROW_NUMBER() OVER (PARTITION BY subquery.set_name) AS row_num
                FROM ({query}) AS subquery
                GROUP BY subquery.set_name, subquery.osm_ids, subquery.geom, subquery.tags, subquery.primitive_type
            ) AS deduped
            WHERE deduped.row_num <= {limit};"""
    ).format(query=union, limit=sql.Literal(get_max_nodes_per_set() + 1))

    return final_query
