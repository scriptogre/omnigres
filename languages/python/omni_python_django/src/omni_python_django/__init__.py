# Django expects backend modules to expose DatabaseWrapper from <module>.base.
from .base import DatabaseWrapper

__all__ = ["DatabaseWrapper"]
