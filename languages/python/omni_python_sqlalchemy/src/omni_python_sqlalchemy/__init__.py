"""SQLAlchemy dialect for omni_python.

Subclasses the pg8000 dialect because that one is also pure-Python and routes
all I/O through DB-API rather than psycopg2's libpq-specific paths. We override
just what is different about running inside the database: there is no real
connection to open, no wire-level BEGIN to issue.

SQLAlchemy's pg8000 dialect expects every connection to expose a `py_types`
dict (a pg8000 extension, not PEP 249). We decorate the connect callable so
each connection carries this dict; nothing about that detail leaks into
omni_python.dbapi itself.
"""

import datetime
import decimal
import types
import uuid

from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000

from omni_python import dbapi as _dbapi


# Python type to PG OID. SQLAlchemy's pg8000 dialect reads this to learn the
# parameter type for each binding. Our cursor.execute does its own inference,
# so the encoder slot is identity.
def _build_py_types():
    nop = lambda v: v  # noqa: E731

    class _PyTypes(dict):
        def __missing__(self, key):
            return (25, nop)  # text OID, no-op encoder

    return _PyTypes({
        bool: (16, nop),
        bytes: (17, nop),
        bytearray: (17, nop),
        memoryview: (17, nop),
        str: (25, nop),
        int: (20, nop),
        float: (701, nop),
        decimal.Decimal: (1700, nop),
        datetime.date: (1082, nop),
        datetime.time: (1083, nop),
        datetime.datetime: (1184, nop),
        datetime.timedelta: (1186, nop),
        uuid.UUID: (2950, nop),
        dict: (3802, nop),
        list: (1009, nop),
        tuple: (1009, nop),
        type(None): (25, nop),
    })


def _connect(*args, **kwargs):
    conn = _dbapi.connect(*args, **kwargs)
    conn.py_types = _build_py_types()
    return conn


# Module-like shim returned to SQLAlchemy via import_dbapi(). Same module
# surface as omni_python.dbapi, but connect() returns connections with
# py_types attached.
_shim = types.ModuleType("omni_python_sqlalchemy._dbapi")
for _attr in dir(_dbapi):
    if not _attr.startswith("_"):
        setattr(_shim, _attr, getattr(_dbapi, _attr))
_shim.connect = _connect


class PGDialect_omni_python(PGDialect_pg8000):
    driver = "omni_python"
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls):
        return _shim

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
        return True

    def do_terminate(self, dbapi_connection):
        pass


__all__ = ["PGDialect_omni_python"]
