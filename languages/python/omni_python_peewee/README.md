# omni_python_peewee

Peewee adapter for omni_python. Lets Peewee Models run inside Postgres via
plpy, with no network round-trip.

## Usage

```python
from omni_python_peewee import OmniPythonDatabase
import peewee

db = OmniPythonDatabase('omni')  # database name ignored

class Person(peewee.Model):
    name = peewee.CharField()
    class Meta:
        database = db
```
