from .construct_nwrs import construct_nwr_cte
from .construct_cluster import construct_cluster_cte


def construct_ctes(spot_query):
    """
    Constructs a list of SQL Common Table Expressions (CTEs)
    from the input SPOT query, one for each node.

    The search area is not a CTE of its own: each node CTE filters on it
    directly, see `construct_search_area_filter`.

    Args:
        spot_query (dict): A dictionary containing the query definition,
            specifically the "nodes" list which defines processing steps.

    Returns:
        list: A list of CTE SQL statements as strings or SQL objects,
            constructed from the node definitions.
    """
    return [construct_cte(node) for node in spot_query["nodes"]]


def construct_cte(node):
    """
    Constructs a specific Common Table Expressions (CTE) based on the node type.

    Args:
        node (dict): A dictionary describing a node in the SPOT query graph.
            Must include a "type" key, which determines how the CTE is constructed.

    Returns:
        SQL object or string: A Common Table Expression corresponding
        to the node type ("cluster" or "nwr").

    Raises:
        ValueError: If the node type is unsupported (not handled).
    """
    # Get the type of the node
    type = node["type"]

    # Depending on the type of the node, construct the appropriate type of CTE
    if type == "cluster":
        return construct_cluster_cte(node)

    elif type == "nwr":
        return construct_nwr_cte(node)
