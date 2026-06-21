"""Django backend that routes through omni_python.dbapi.

Subclasses Django's stock postgresql backend so we inherit the schema editor,
introspection, operations, etc. Only the driver itself differs: ours is
in-process via plpy.
"""

from django.db.backends.postgresql import base as pg_base

from omni_python import dbapi as Database


class DatabaseWrapper(pg_base.DatabaseWrapper):
    vendor = "postgresql"
    display_name = "PostgreSQL (omni_python)"
    Database = Database

    def get_connection_params(self):
        # Ignore the DATABASES['default'] dict; we are already inside the DB.
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
        # Mirror our DB-API's autocommit knob. Django flips this to control
        # transaction boundaries; our connection respects it.
        self.connection.autocommit = autocommit
