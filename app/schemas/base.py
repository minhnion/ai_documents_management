from typing import Generic, TypeVar

from pydantic import BaseModel

DataT = TypeVar("DataT")


class BaseResponse(BaseModel, Generic[DataT]):
    """Generic wrapper for all API responses."""

    success: bool = True
    message: str = "OK"
    data: DataT | None = None
