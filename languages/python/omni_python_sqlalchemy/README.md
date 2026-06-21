# omni_python_sqlalchemy

SQLAlchemy dialect for omni_python. Lets SQLAlchemy run inside Postgres via plpy,
with no network round-trip.

## Usage

```python
import sqlalchemy as sa
engine = sa.create_engine("postgresql+omni_python://")
with engine.connect() as conn:
    print(conn.execute(sa.text("SELECT 1")).scalar())
```

The URL is ignored: we are already inside the database.
