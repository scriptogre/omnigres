# omni_python_django

Django database backend for omni_python. Lets Django Models run inside Postgres
via plpy, with no network round-trip.

## Usage

```python
settings.configure(
    DATABASES={'default': {'ENGINE': 'omni_python_django', 'NAME': 'omni'}},
    INSTALLED_APPS=[],
)
django.setup()
```

Name and other connection params are ignored: we are already inside the database.
