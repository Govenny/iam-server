from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel

T = TypeVar("T")


class ResponseModel(BaseModel, Generic[T]):
    code: int = 200
    message: str = "success"
    data: Optional[T] = None


class ErrorResponseModel(BaseModel):
    code: int = 400
    message: str
    detail: Optional[Any] = None
