from collections import defaultdict
from .utils import distance_to_meters
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


# Name of the CTE holding the rows that take part in a relation, and aliases
# used around it. Node CTEs are named after the numeric node id and aliased
# with the node name, so these are prefixed to stay clear of both.
MATCHED_CTE = "__matched"
MATCHED_ALIAS = "__m"
MATCHED_ROW_ALIAS = "__row"


def construct_edge_condition(edge, id_to_name):
    """Build the SQL join condition for one edge of the graph.

    Args:
      edge (dict): `{"source": <node_id>, "target": <node_id>, "type": ...}`,
        plus `"value"` (e.g. "50m", "0.2km") when the type is "distance".
      id_to_name (dict): Maps node ids to node names, which are the SQL aliases.

    Returns:
      psycopg2.sql.Composed: `ST_DWithin(...)` for a "distance" edge,
      `ST_Intersects(...)` for a "contains" edge.

    Raises:
      ValueError: If the edge has an unknown type.
    """
    source_alias = sql.Identifier(id_to_name[edge["source"]])
    target_alias = sql.Identifier(id_to_name[edge["target"]])

    if edge["type"] == "distance":
        # Geometries are already projected to UTM, so ST_DWithin measures
        # metres. An H3 pre-filter used to narrow the candidate pairs first,
        # but the h3 extension is not installed on the database, which made
        # every query with a distance relation fail outright.
        return sql.SQL(
            "ST_DWithin({source_alias}.transformed_geom, {target_alias}.transformed_geom, {distance})"
        ).format(
            source_alias=source_alias,
            target_alias=target_alias,
            distance=sql.Literal(distance_to_meters(edge["value"])),
        )

    if edge["type"] == "contains":
        return sql.SQL(
            "ST_Intersects({source_alias}.transformed_geom, {target_alias}.transformed_geom)"
        ).format(source_alias=source_alias, target_alias=target_alias)

    raise ValueError("unknownEdgeType")


def order_joins(nodes, edges, id_to_name):
    """Group the nodes connected by edges and put each group in a joinable order.

    Each group is a connected component of the graph. Within a group every node
    after the first is connected to at least one node before it, so it can be
    joined with an ON clause that only refers to nodes already in the FROM
    list. Nodes without any edge are in no group.

    Args:
      nodes (list[dict]): The nodes of the graph.
      edges (list[dict]): The edges of the graph.
      id_to_name (dict): Maps node ids to node names.

    Returns:
      list[list[tuple]]: One list per group, holding `(node_id, conditions)` in
      join order. `conditions` are the SQL conditions of all edges between that
      node and the nodes before it; it is empty for the first node of a group.
    """
    neighbours = defaultdict(list)
    for edge in edges:
        if edge["source"] == edge["target"]:
            raise ValueError("selfReferencingEdge")

        condition = construct_edge_condition(edge, id_to_name)
        neighbours[edge["source"]].append((edge["target"], condition))
        neighbours[edge["target"]].append((edge["source"], condition))

    node_ids = [node["id"] for node in nodes]
    placed = set()
    groups = []

    for start_id in node_ids:
        if start_id in placed or start_id not in neighbours:
            continue

        group = [(start_id, [])]
        placed.add(start_id)
        members = {start_id}

        while True:
            # The next node that has an edge to a node already in the group.
            next_id = next(
                (
                    node_id
                    for node_id in node_ids
                    if node_id not in placed
                    and any(other in members for other, _ in neighbours.get(node_id, []))
                ),
                None,
            )
            if next_id is None:
                break

            conditions = [
                condition
                for other, condition in neighbours[next_id]
                if other in members
            ]
            group.append((next_id, conditions))
            placed.add(next_id)
            members.add(next_id)

        groups.append(group)

    return groups


def construct_matched_query(group, id_to_name):
    """Build the query that joins one group of related nodes.

    The join runs once and emits, for every combination of rows that satisfies
    all relations, one `(set_name, osm_ids)` row per node. Only these keys are
    carried through the join and deduplicated; geometry and tags are looked up
    afterwards.

    Args:
      group (list[tuple]): `(node_id, conditions)` in join order, as returned by
        `order_joins`.
      id_to_name (dict): Maps node ids to node names.

    Returns:
      psycopg2.sql.Composed: A `SELECT DISTINCT set_name, osm_ids ...` query.
    """
    first_id, _ = group[0]
    from_clause = sql.SQL("{id} {alias}").format(
        id=sql.Identifier(str(first_id)),
        alias=sql.Identifier(id_to_name[first_id]),
    )

    joins = [
        sql.SQL("JOIN {id} {alias} ON {conditions}").format(
            id=sql.Identifier(str(node_id)),
            alias=sql.Identifier(id_to_name[node_id]),
            conditions=sql.SQL(" AND ").join(conditions),
        )
        for node_id, conditions in group[1:]
    ]

    keys = [
        sql.SQL("({set_name}::text, {alias}.osm_ids)").format(
            set_name=sql.Literal(id_to_name[node_id]),
            alias=sql.Identifier(id_to_name[node_id]),
        )
        for node_id, _ in group
    ]

    return sql.SQL(
        """
                SELECT DISTINCT {row}.set_name, {row}.osm_ids
                FROM {from_clause}
                {joins}
                CROSS JOIN LATERAL (VALUES {keys}) AS {row}(set_name, osm_ids)"""
    ).format(
        row=sql.Identifier(MATCHED_ROW_ALIAS),
        from_clause=from_clause,
        joins=sql.SQL(" ").join(joins),
        keys=sql.SQL(", ").join(keys),
    )


def construct_relations(spot_query):
    """Build the SQL that relates the node CTEs of a graph and selects the result.

    Nodes are the CTEs built by `construct_ctes` (named after the node id), and
    edges describe spatial relations between them. Supported relations:

    - `"distance"`: Uses `ST_DWithin(source.transformed_geom, target.transformed_geom, meters)`
      where the distance is parsed and normalized to meters via `distance_to_meters`.
    - `"contains"`: Uses `ST_Intersects(source.transformed_geom, target.transformed_geom)`.

    A row of a node that has edges is part of the result if there is a
    combination of rows, one from each node it is connected to directly or
    indirectly, that satisfies all relations between them. A node without edges
    contributes all of its rows.

    The query is built in two steps:

    1. A `__matched` CTE joins each group of connected nodes once and keeps the
       distinct `(set_name, osm_ids)` of the rows taking part.
    2. The final SELECT reads `set_name`, `osm_ids`, `geom`, `tags` and
       `primitive_type` for those rows from the node CTEs, adds the nodes
       without edges, and cuts every set at `MAX_NODES_PER_SET` + 1 rows.

    The join used to run once per node, and its output was deduplicated by
    grouping on the geometry and the tags of every joined row. Now it runs
    once, and what gets deduplicated is a pair of short values.

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
        - Each `nodes[i]["id"]` is the name of that node's CTE (cast to text).
        - Each `nodes[i]["name"]` becomes the SQL table alias for that node.

    Returns:
      tuple[list, psycopg2.sql.Composed]:
        The CTEs to add to the node CTEs (empty if the graph has no edges), and
        the final SELECT. `construct_query_from_graph` puts them together.

    Raises:
      ValueError: If an edge references the same source and target node
        (`"selfReferencingEdge"`) or has an unknown type (`"unknownEdgeType"`).

    Implementation details:
      - Uses `psycopg2.sql.Identifier` and `psycopg2.sql.Literal` to avoid SQL injection.
      - A node CTE with edges is referenced twice, by the join and by the final
        SELECT. PostgreSQL therefore materializes it, so its filter runs once
        and `ST_Transform` is evaluated once per row, not once per compared pair.
    """
    edges = spot_query.get(
        "edges", []
    ) or []  # Get edges from input map relation, set to empty list if not found
    nodes = spot_query.get("nodes", None)  # Get nodes from input map relation

    # Creating a mapping from node IDs to node names
    id_to_name = {node["id"]: node["name"] for node in nodes}

    groups = order_joins(nodes, edges, id_to_name)
    related_ids = {node_id for group in groups for node_id, _ in group}

    ctes = []
    if groups:
        matched = sql.SQL(" UNION ALL ").join(
            construct_matched_query(group, id_to_name) for group in groups
        )
        ctes.append(
            sql.SQL("{matched} AS ({query})").format(
                matched=sql.Identifier(MATCHED_CTE), query=matched
            )
        )

    # One SELECT per node
    final_queries = []

    for node in nodes:
        query_part = sql.SQL(
            """
                    SELECT
                        {type}::text AS set_name,
                        {name_alias}.osm_ids,
                        {name_alias}.geom,
                        {name_alias}.tags,
                        {name_alias}.primitive_type
                    FROM {id} {name_alias}"""
        ).format(
            type=sql.Literal(node["name"]),
            name_alias=sql.Identifier(node["name"]),
            id=sql.Identifier(str(node["id"])),
        )

        if node["id"] in related_ids:
            # Keep only the rows that take part in the node's relations
            query_part += sql.SQL(
                """
                    JOIN {matched} {m}
                        ON {m}.set_name = {type} AND {m}.osm_ids = {name_alias}.osm_ids"""
            ).format(
                matched=sql.Identifier(MATCHED_CTE),
                m=sql.Identifier(MATCHED_ALIAS),
                type=sql.Literal(node["name"]),
                name_alias=sql.Identifier(node["name"]),
            )

        final_queries.append(query_part)

    # Combine all SQL queries using UNION ALL
    union = sql.SQL(" UNION ALL ").join(final_queries)

    final_query = sql.SQL(
        """
            SELECT
                limited.set_name,
                limited.osm_ids,
                limited.geom,
                limited.tags,
                limited.primitive_type
            FROM (
                SELECT
                    subquery.set_name,
                    subquery.osm_ids,
                    subquery.geom,
                    subquery.tags,
                    subquery.primitive_type,
                    ROW_NUMBER() OVER (PARTITION BY subquery.set_name) AS row_num
                FROM ({query}) AS subquery
            ) AS limited
            WHERE limited.row_num <= {limit};"""
    ).format(query=union, limit=sql.Literal(get_max_nodes_per_set() + 1))

    return ctes, final_query
