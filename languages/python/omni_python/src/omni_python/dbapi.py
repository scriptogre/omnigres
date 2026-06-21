"""
Python DB-API 2.0 driver for omni_python, on top of PL/Python's plpy.

PEP 249: https://peps.python.org/pep-0249/

Skeleton from plpydbapi by Peter Eisentraut (PostgreSQL License).
Type-mapping and exception-classification ideas inspired by pg8000 (BSD-3)
and psycopg2's sqlstate categories.
"""

import datetime
import decimal
import json
import time
import uuid

import plpy


# --- PEP 249 module globals ---

apilevel = "2.0"
threadsafety = 1  # Threads may share the module, not connections/cursors.
paramstyle = "format"  # %s placeholders, converted to $N internally.


# --- Exception hierarchy (PEP 249 section 6.1) ---

class Warning(Exception):  # noqa: A001, DB-API mandates this name
    pass


class Error(Exception):
    """Base of all DB-API errors. Carries pgcode/diag from plpy.SPIError."""

    def __init__(self, message="", spierror=None):
        super().__init__(message)
        self.spierror = spierror
        if spierror is not None:
            self.pgcode = getattr(spierror, "sqlstate", None)
            self.pgerror = str(spierror)
            self.diag = _Diag(spierror)
        else:
            self.pgcode = None
            self.pgerror = None
            self.diag = None


class InterfaceError(Error): pass
class DatabaseError(Error): pass
class DataError(DatabaseError): pass
class OperationalError(DatabaseError): pass
class IntegrityError(DatabaseError): pass
class InternalError(DatabaseError): pass
class ProgrammingError(DatabaseError): pass
class NotSupportedError(DatabaseError): pass


class _Diag:
    """psycopg2-style diagnostics object attached to Error."""
    __slots__ = ("sqlstate", "message_primary", "message_detail", "message_hint",
                 "context", "schema_name", "table_name", "column_name",
                 "datatype_name", "constraint_name")

    def __init__(self, e):
        self.sqlstate = getattr(e, "sqlstate", None)
        self.message_primary = str(e)
        self.message_detail = getattr(e, "detail", None)
        self.message_hint = getattr(e, "hint", None)
        for attr in ("context", "schema_name", "table_name", "column_name",
                     "datatype_name", "constraint_name"):
            setattr(self, attr, getattr(e, attr, None))


# Postgres SQLSTATE class code to DB-API exception subclass.
# https://www.postgresql.org/docs/current/errcodes-appendix.html
_SQLSTATE_CLASS_MAP = {
    "0A": NotSupportedError,
    "20": ProgrammingError, "21": ProgrammingError,
    "22": DataError,
    "23": IntegrityError,
    "24": InternalError, "25": InternalError,
    "26": ProgrammingError, "27": OperationalError, "28": OperationalError,
    "2B": IntegrityError, "2D": InternalError, "2F": OperationalError,
    "34": ProgrammingError,
    "38": InternalError, "39": InternalError, "3B": InternalError,
    "3D": ProgrammingError, "3F": ProgrammingError,
    "40": OperationalError,
    "42": ProgrammingError, "44": ProgrammingError,
    "53": OperationalError, "54": OperationalError, "55": OperationalError,
    "57": OperationalError, "58": OperationalError,
    "F0": InternalError, "P0": InternalError, "XX": InternalError,
}


def _classify(spierror):
    sqlstate = getattr(spierror, "sqlstate", None) or ""
    cls = _SQLSTATE_CLASS_MAP.get(sqlstate[:2], DatabaseError)
    return cls(str(spierror), spierror=spierror)


# --- Result-side type conversion ---
# plpy converts only a handful of base types (bool, int*, float*, numeric,
# text, bytea) to Python objects. Everything else comes back as the PG string
# representation. We map the well-known OIDs to Python builtins here.

def _parse_timestamp(s):
    return datetime.datetime.fromisoformat(s.replace(" ", "T", 1))


def _parse_timestamptz(s):
    s = s.replace(" ", "T", 1)
    # PG may emit '+00' or '+0000' rather than '+00:00'; pad to ISO 8601.
    if len(s) >= 3 and s[-3] in "+-" and ":" not in s[-3:]:
        s = s + ":00"
    return datetime.datetime.fromisoformat(s)


# PG type OID, see src/include/catalog/pg_type.dat.
_RESULT_CONVERTERS = {
    1082: datetime.date.fromisoformat,    # date
    1083: datetime.time.fromisoformat,    # time
    1114: _parse_timestamp,               # timestamp
    1184: _parse_timestamptz,             # timestamptz
    2950: uuid.UUID,                      # uuid
}


def _convert_row(row, description):
    """Apply OID-based converters to each string-valued cell in a row."""
    out = []
    for i, val in enumerate(row):
        if isinstance(val, str) and description:
            conv = _RESULT_CONVERTERS.get(description[i][1])
            if conv is not None:
                try:
                    val = conv(val)
                except (ValueError, TypeError):
                    pass  # leave as string on parse failure
        out.append(val)
    return tuple(out)


# --- Type mapping (param side) ---

def _sql_literal(value):
    """Render a Python value as a Postgres SQL literal. For cursor.mogrify."""
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


def _json_default(obj):
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    if isinstance(obj, (datetime.date, datetime.time, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _pg_type_for(value):
    """Return (pg_type_name, coerced_value) for one Python parameter."""
    if value is None:
        return ("text", None)
    if isinstance(value, bool):  # before int (bool is a subclass of int)
        return ("bool", value)
    if isinstance(value, int):
        return ("int8", value)
    if isinstance(value, float):
        return ("float8", value)
    if isinstance(value, decimal.Decimal):
        return ("numeric", value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ("bytea", bytes(value))
    if isinstance(value, datetime.datetime):
        return (("timestamptz" if value.tzinfo else "timestamp"), value)
    if isinstance(value, datetime.date):
        return ("date", value)
    if isinstance(value, datetime.time):
        return ("time", value)
    if isinstance(value, datetime.timedelta):
        return ("interval", value)
    if isinstance(value, uuid.UUID):
        return ("uuid", str(value))
    if isinstance(value, dict):
        return ("jsonb", json.dumps(value, default=_json_default))
    if isinstance(value, (list, tuple)):
        if value:
            elem_type, _ = _pg_type_for(value[0])
            return (f"{elem_type}[]", list(value))
        return ("text[]", [])
    if isinstance(value, str):
        return ("text", value)
    return ("text", str(value))


# --- Default Python-type to PG-OID mapping ---
# SQLAlchemy's pg8000 dialect looks up py_types[T] to learn the OID for a
# parameter of Python type T. Our cursor.execute does its own type inference
# via _pg_type_for, so the encoder slot is a no-op identity.

class _PyTypes(dict):
    """Dict-like: missing Python types fall back to text+no-op."""
    def __missing__(self, key):
        return (25, lambda v: v)  # text OID, identity encoder


def _default_py_types():
    nop = lambda v: v  # noqa: E731
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


# --- Module-level API ---

def connect(dsn=None, **kwargs):
    """Return a Connection. Accepts and ignores DSN/kwargs so SQLAlchemy-style
    URLs do not blow up; we are already inside the database."""
    return Connection()


# --- Connection ---

class Connection:
    Warning = Warning
    Error = Error
    InterfaceError = InterfaceError
    DatabaseError = DatabaseError
    DataError = DataError
    OperationalError = OperationalError
    IntegrityError = IntegrityError
    InternalError = InternalError
    ProgrammingError = ProgrammingError
    NotSupportedError = NotSupportedError

    def __init__(self):
        self.closed = False
        # Match psycopg2/PEP 249: connections start transactional. SQLAlchemy
        # and most ORMs expect this; flip explicitly to True for fire-and-forget
        # statement execution.
        self.autocommit = False
        self._subxact = None
        self._server_version_cache = None
        # SQLAlchemy's pg8000 dialect looks up py_types[T] and may also
        # register extra encoders here. We accept everything; plpy handles
        # the real encoding so the entries are essentially metadata.
        self.py_types = _default_py_types()

    @property
    def server_version(self):
        """PG server version as an int (psycopg2-compatible). Peewee reads this."""
        if self._server_version_cache is None:
            try:
                row = plpy.execute("SHOW server_version_num")[0]
                self._server_version_cache = int(row["server_version_num"])
            except Exception:
                self._server_version_cache = 0
        return self._server_version_cache

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.closed:
            return
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    def close(self):
        if self.closed:
            raise InterfaceError("Connection already closed")
        self.rollback()
        self.closed = True

    def _ensure_transaction(self):
        if self._subxact is None:
            self._subxact = plpy.subtransaction()
            self._subxact.enter()

    def commit(self):
        if self.closed:
            raise InterfaceError("Connection is closed")
        if self._subxact is not None:
            self._subxact.exit(None, None, None)
            self._subxact = None

    def rollback(self):
        if self.closed:
            raise InterfaceError("Connection is closed")
        if self._subxact is not None:
            self._subxact.exit("rollback", None, None)
            self._subxact = None

    def cursor(self):
        if self.closed:
            raise InterfaceError("Connection is closed")
        return Cursor(self)


# --- Cursor ---

# SPI status codes from PG (executor/spi.h). Inlined to avoid C import.
_SPI_OK_UTILITY = 4
_SPI_OK_SELECT = 5
_SPI_OK_INSERT_RETURNING = 11
_SPI_OK_DELETE_RETURNING = 12
_SPI_OK_UPDATE_RETURNING = 13
_RESULT_STATUSES = {_SPI_OK_SELECT, _SPI_OK_INSERT_RETURNING,
                    _SPI_OK_DELETE_RETURNING, _SPI_OK_UPDATE_RETURNING}


class Cursor:
    arraysize = 1

    def __init__(self, connection):
        self.connection = connection
        self.closed = False
        self.description = None
        self.rowcount = -1
        self.rownumber = None
        self.lastrowid = None
        self._rows = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    next = __next__  # legacy

    def close(self):
        self.closed = True

    def _check_open(self):
        if self.closed:
            raise InterfaceError("Cursor is closed")
        if self.connection.closed:
            raise InterfaceError("Connection is closed")

    def execute(self, operation, parameters=None):
        self._check_open()
        self.connection._ensure_transaction()

        parameters = list(parameters) if parameters else []
        types, values, placeholders = [], [], []
        for i, param in enumerate(parameters):
            pg_type, coerced = _pg_type_for(param)
            types.append(pg_type)
            values.append(coerced)
            placeholders.append(f"${i+1}")

        try:
            if parameters and "%s" in operation:
                query = operation % tuple(placeholders)
            else:
                query = operation  # native $N or no params
            if types:
                plan = plpy.prepare(query, types)
                res = plpy.execute(plan, values)
            else:
                res = plpy.execute(query)
        except plpy.SPIError as e:
            # Roll back the subxact so the connection is usable again. PG's
            # transaction state after an error is "aborted"; any further
            # statement in the same subxact would also fail. The next
            # execute() will get a fresh subxact via _ensure_transaction.
            if self.connection._subxact is not None:
                try:
                    self.connection._subxact.exit("error", None, None)
                finally:
                    self.connection._subxact = None
            raise _classify(e) from e

        self._rows = None
        self.rownumber = None
        self.description = None
        self.rowcount = -1

        status = res.status()
        # Gate on column metadata, not status: SHOW etc. return rows under
        # SPI_OK_UTILITY, and empty SELECTs still have column descriptions.
        # plpy raises plpy.Error (not just AttributeError) when there is no
        # result set, hence the broad except.
        try:
            cols = res.colnames()
        except Exception:
            cols = None

        if cols:
            coltypes = res.coltypes()
            self.description = [
                (n, t, None, None, None, None, None)
                for n, t in zip(cols, coltypes)
            ]
            raw_rows = [tuple(row[col] for col in row.keys()) for row in res]
            self._rows = [_convert_row(r, self.description) for r in raw_rows]
            self.rownumber = 0
            self.rowcount = len(self._rows)
        elif status == _SPI_OK_UTILITY:
            self.rowcount = -1
        else:
            self.rowcount = res.nrows()

        if self.connection.autocommit:
            self.connection.commit()

        return self

    def executemany(self, operation, seq_of_parameters):
        self._check_open()
        total = 0
        for params in seq_of_parameters:
            self.execute(operation, params)
            if self.rowcount >= 0 and total >= 0:
                total += self.rowcount
            else:
                total = -1
        self.rowcount = total

    def fetchone(self):
        self._check_open()
        # PEP 249 says raise if no result set, but SQLAlchemy calls fetchone()
        # routinely after DDL etc. Returning None is the practical convention.
        if not self._rows or self.rownumber >= len(self._rows):
            return None
        row = self._rows[self.rownumber]
        self.rownumber += 1
        return row

    def fetchmany(self, size=None):
        self._check_open()
        if not self._rows:
            return []
        if size is None:
            size = self.arraysize
        end = min(self.rownumber + size, len(self._rows))
        result = self._rows[self.rownumber:end]
        self.rownumber = end
        return result

    def fetchall(self):
        self._check_open()
        if not self._rows:
            return []
        result = self._rows[self.rownumber:]
        self.rownumber = len(self._rows)
        return result

    def scroll(self, value, mode="relative"):
        self._check_open()
        if self._rows is None:
            raise InterfaceError("No result set")
        if mode == "relative":
            newpos = self.rownumber + value
        elif mode == "absolute":
            newpos = value
        else:
            raise ProgrammingError(f"Invalid scroll mode: {mode}")
        if newpos < 0 or newpos > len(self._rows):
            raise IndexError("scroll out of range")
        self.rownumber = newpos

    def setinputsizes(self, sizes): pass
    def setoutputsize(self, size, column=None): pass

    def mogrify(self, operation, parameters=None):
        """psycopg2-compatible: return SQL with parameters rendered inline.

        Returns bytes, matching psycopg2 (Django calls .decode() on it).
        """
        if not parameters:
            result = operation
        else:
            rendered = tuple(_sql_literal(p) for p in parameters)
            result = operation % rendered if "%s" in operation else operation
        return result.encode("utf-8") if isinstance(result, str) else result


# --- DB-API 2.0 type constructors (PEP 249 section 7) ---

def Date(year, month, day):
    return datetime.date(year, month, day)


def Time(hour, minute, second):
    return datetime.time(hour, minute, second)


def Timestamp(year, month, day, hour, minute, second):
    return datetime.datetime(year, month, day, hour, minute, second)


def DateFromTicks(ticks):
    return datetime.date(*time.localtime(ticks)[:3])


def TimeFromTicks(ticks):
    return datetime.time(*time.localtime(ticks)[3:6])


def TimestampFromTicks(ticks):
    return datetime.datetime(*time.localtime(ticks)[:6])


def Binary(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return value
    return bytes(value)


# DB-API type-object sentinels (PEP 249 section 7.2).
class STRING: pass
class BINARY: pass
class NUMBER: pass
class DATETIME: pass
class ROWID: pass


__all__ = [
    "apilevel", "threadsafety", "paramstyle", "connect",
    "Warning", "Error", "InterfaceError", "DatabaseError",
    "DataError", "OperationalError", "IntegrityError", "InternalError",
    "ProgrammingError", "NotSupportedError",
    "Connection", "Cursor",
    "Date", "Time", "Timestamp",
    "DateFromTicks", "TimeFromTicks", "TimestampFromTicks", "Binary",
    "STRING", "BINARY", "NUMBER", "DATETIME", "ROWID",
]
