"""Peewee adapter for omni_python.

Subclasses peewee.PostgresqlDatabase and routes _connect() through our DB-API.
"""

import peewee
from omni_python import dbapi


class OmniPythonDatabase(peewee.PostgresqlDatabase):
    """Peewee Postgres database backed by omni_python's in-DB DB-API."""

    def _connect(self):
        # The database name and connect kwargs are ignored: we are already
        # inside the database.
        return dbapi.connect()


__all__ = ["OmniPythonDatabase"]
