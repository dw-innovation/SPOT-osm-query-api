import re
from psycopg2 import sql

from ..utils import distance_to_meters

def construct_cte_where_clause(filters):
    """
    Constructs a SQL WHERE clause for filtering OSM features based on tag conditions
    and spatial intersection with the defined envelope.

    Args:
        filters (list): A list of dictionaries representing filter conditions.
                        Each filter can use logical operators ("and", "or") or
                        direct key/operator/value conditions.

    Returns:
        psycopg2.sql.Composed: A composed SQL WHERE clause combining the envelope
        intersection filter and user-defined tag filters.

    Notes:
        - Always adds a spatial envelope filter: `geom && (SELECT geom FROM envelope)`.
        - Returns an empty string if no filters are provided.
    """

    if not filters:
        return ""

    area_filter = sql.SQL("geom && (SELECT geom FROM envelope)")
    where_filters = [construct_filter(f) for f in filters]

    if area_filter:
        where_filters.insert(0, area_filter)

    return sql.SQL(" AND ").join(where_filters)


def sanitize_for_regex(value):
    """
    Cleans a string by removing all non-alphanumeric characters,
    for safe use in SQL regex expressions.

    Args:
        value (str or any): The string to sanitize. Non-string values are returned as-is.

    Returns:
        str or original type: The sanitized string or original input if not a string.
    """
    if isinstance(value, str):
        return re.sub(r'[^A-Za-z0-9]', '', value)
    return value


def construct_filter(filter):
    """
    Recursively constructs a SQL condition from a filter dictionary.

    Supports:
      - Logical combinations with "and"/"or".
      - Basic key/operator/value filters.
      - Special handling for numeric comparisons and regex matches.

    Args:
        filter (dict): A filter clause. Can be a logical group (with "and"/"or") or
                       a leaf filter with "key", "operator", and "value".

    Returns:
        psycopg2.sql.Composed: A SQL expression representing the filter clause.

    Notes:
        - Converts distance units for numeric comparisons involving tags like "height".
        - Translates `***any***` value to `tags ? key` (tag presence).
        - Regex values are sanitized before being embedded in the query.
        - Supports numeric filtering with type casting.
    """
    if "and" in filter:
        return sql.SQL("({})").format(
            sql.SQL(" AND ").join([construct_filter(f) for f in filter["and"]])
        )

    if "or" in filter:
        return sql.SQL("({})").format(
            sql.SQL(" OR ").join([construct_filter(f) for f in filter["or"]])
        )

    key = filter.get("key", "key")
    operator = filter.get("operator", "operator")
    value = filter.get("value", "value")

    if key in ["height", "width", "length"] or operator in [">", "<"]:
        value = distance_to_meters(value)

    if value == "***any***":
        return sql.SQL("tags ? {key}").format(key=sql.Literal(key))

    if operator == "~":
        value = sanitize_for_regex(value)
        sql_template = sql.SQL(
            "LOWER(REGEXP_REPLACE(tags->>{key}, '[^A-Za-z0-9]', '', 'g')) ~ LOWER({value})"
        ).format(
            key=sql.Literal(key),
            value=sql.Literal(value)
        )

    elif operator in [">", "<"]:
        sql_template = sql.SQL(
            """
            CASE 
                WHEN tags->> {key} ~ '^[0-9]+(\\.[0-9]+)?$'
                THEN CAST(tags->> {key} AS FLOAT) {operator} {value}
                ELSE FALSE 
            END
            """
        ).format(
            key=sql.Literal(key),
            operator=sql.SQL(operator),
            value=sql.Literal(value)
        )

    else:
        sql_template = sql.SQL("tags->> {key} = {value}").format(
            key=sql.Literal(key),
            value=sql.Literal(value)
        )

    return sql_template
