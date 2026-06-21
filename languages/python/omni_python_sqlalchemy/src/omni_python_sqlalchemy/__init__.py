"""SQLAlchemy dialect for omni_python.

Subclasses the pg8000 dialect because that one is also pure-Python and routes
all I/O through DB-API rather than psycopg2's libpq-specific paths. We override
just what is different about running inside the database: there is no real
connection to open, no wire-level BEGIN to issue.
"""

from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000


class PGDialect_omni_python(PGDialect_pg8000):
    driver = "omni_python"
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls):
        from omni_python import dbapi
        return dbapi

    # SQLAlchemy < 2.0 calls dbapi(), 2.0+ calls import_dbapi().
    dbapi = import_dbapi

    def create_connect_args(self, url):
        # URL is irrelevant; we are already in the database.
        return ([], {})

    def do_begin(self, dbapi_connection):
        # plpy already runs inside a transaction. Our DB-API uses
        # subtransactions per execute when autocommit is on, or a single
        # subtransaction across the connection when off. Either way there is
        # no wire-level BEGIN to send.
        pass

    def do_commit(self, dbapi_connection):
        dbapi_connection.commit()

    def do_rollback(self, dbapi_connection):
        dbapi_connection.rollback()

    def do_ping(self, dbapi_connection):
        # We are already inside PG; the connection is always live.
        return True

    def do_terminate(self, dbapi_connection):
        # Same reason as do_ping.
        pass


__all__ = ["PGDialect_omni_python"]
