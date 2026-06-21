"""Peewee adapter for omni_python.

Subclasses peewee.PostgresqlDatabase and routes _connect() through our DB-API.

Peewee's Postgres backend reads `connection.server_version` (a psycopg2
attribute, not PEP 249). We attach it here per connection rather than
polluting omni_python.dbapi with a per-ORM accessor.
"""

import peewee

from omni_python import dbapi


def _server_version_num():
    """Look up the running PG server version. Lazy; only called on first
    Peewee connect. plpy is only importable from inside a PL/Python call."""
    import plpy
    row = plpy.execute("SHOW server_version_num")[0]
    return int(row["server_version_num"])


class OmniPythonDatabase(peewee.PostgresqlDatabase):
    """Peewee Postgres database backed by omni_python's in-DB DB-API."""

    def _connect(self):
        conn = dbapi.connect()
        conn.server_version = _server_version_num()
        return conn


__all__ = ["OmniPythonDatabase"]
