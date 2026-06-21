"""psycopg2-style mogrify() for the Django backend.

Renders a SQL string with parameters substituted inline. Django uses this
for batched INSERTs and some logging paths. Returns bytes (matches psycopg2;
Django calls .decode() on the result).
"""

import datetime
import decimal
import json
import uuid


def _json_default(obj):
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    if isinstance(obj, (datetime.date, datetime.time, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "'\\\\x" + bytes(value).hex() + "'::bytea"
    if isinstance(value, datetime.datetime):
        return f"'{value.isoformat()}'::{'timestamptz' if value.tzinfo else 'timestamp'}"
    if isinstance(value, datetime.date):
        return f"'{value.isoformat()}'::date"
    if isinstance(value, datetime.time):
        return f"'{value.isoformat()}'::time"
    if isinstance(value, datetime.timedelta):
        return f"'{value.total_seconds()} seconds'::interval"
    if isinstance(value, uuid.UUID):
        return f"'{value}'::uuid"
    if isinstance(value, dict):
        s = json.dumps(value, default=_json_default).replace("'", "''")
        return f"'{s}'::jsonb"
    if isinstance(value, (list, tuple)):
        elems = ",".join(_sql_literal(v) for v in value)
        return f"ARRAY[{elems}]"
    s = str(value).replace("'", "''")
    return f"'{s}'"


def mogrify(operation, parameters=None):
    if not parameters:
        result = operation
    else:
        rendered = tuple(_sql_literal(p) for p in parameters)
        result = operation % rendered if "%s" in operation else operation
    return result.encode("utf-8") if isinstance(result, str) else result
