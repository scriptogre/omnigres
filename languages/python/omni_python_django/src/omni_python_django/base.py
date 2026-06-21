"""Django backend that routes through omni_python.dbapi.

Subclasses Django's stock postgresql backend so we inherit the schema editor,
introspection, operations, etc. Only the driver itself differs: ours is
in-process via plpy.

Django reads psycopg2-specific bits from the cursor (cursor.mogrify); we
attach those here per-cursor rather than polluting omni_python.dbapi.
"""

from django.db.backends.postgresql import base as pg_base

from omni_python import dbapi as Database

from . import _mogrify


class DatabaseWrapper(pg_base.DatabaseWrapper):
    vendor = "postgresql"
    display_name = "PostgreSQL (omni_python)"
    Database = Database

    def get_connection_params(self):
        # Ignore DATABASES['default']; we are already inside the DB.
        return {}

    def get_new_connection(self, conn_params):
        return Database.connect()

    def init_connection_state(self):
        # Django's psycopg variant sets timezone / isolation level via SET
        # statements. We skip them: the surrounding plpy transaction inherits
        # the calling session's settings.
        pass

    def is_usable(self):
        return not self.connection.closed

    def _set_autocommit(self, autocommit):
        self.connection.autocommit = autocommit

    def create_cursor(self, name=None):
        cursor = self.connection.cursor()
        # Django calls cursor.mogrify() in batched INSERT paths.
        cursor.mogrify = _mogrify.mogrify
        return cursor
