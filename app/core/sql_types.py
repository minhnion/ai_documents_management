from __future__ import annotations

from typing import Any

from sqlalchemy.types import UserDefinedType

try:
    from pgvector.sqlalchemy import HALFVEC as HALFVEC
except ImportError:
    class HALFVEC(UserDefinedType):
        """Fallback SQLAlchemy type for pgvector's `halfvec(dim)` columns.

        The app does not read/write chunk embeddings yet, so a lightweight
        fallback is sufficient until the pgvector package is installed.
        """

        cache_ok = True

        def __init__(self, dimensions: int) -> None:
            self.dimensions = int(dimensions)

        def get_col_spec(self, **_: Any) -> str:
            return f"halfvec({self.dimensions})"

