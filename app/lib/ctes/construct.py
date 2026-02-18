from flask import g
from .construct_search_area import construct_search_area_cte
from .construct_nwrs import construct_nwr_cte
from .construct_cluster import construct_cluster_cte


def construct_ctes(spot_query):
    """
    Constructs a list of SQL Common Table Expressions (CTEs)
    from the input SPOT query.

    This includes:
      - An envelope CTE based on the global area.
      - Additional CTEs for each node in the SPOT query.

    Args:
        spot_query (dict): A dictionary containing the query definition,
            specifically the "nodes" list which defines processing steps.

    Returns:
        list: A list of CTE SQL statements as strings or SQL objects,
            constructed from the search area and node definitions.
    """
    # Initialize an empty list to hold the CTEs
    ctes = []

    # Extract nodes from the SPOT query
    nodes = spot_query["nodes"]

    # Construct envelope CTE
    envelope_cte = construct_search_area_cte(g.area["type"])

    ctes.append(envelope_cte)
    
    # Iterate over the nodes to construct each CTE
    for i in range(len(nodes)):
        cte = construct_cte(nodes[i])

        # Append the CTE to the list
        ctes.append(cte)

    return ctes


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
