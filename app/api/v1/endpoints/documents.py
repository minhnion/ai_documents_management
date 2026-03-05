from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.api.deps import DBSession, require_roles
from app.services.document_file_service import DocumentFileService

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.get(
    "/{document_id}/file",
    summary="Get Document File",
    responses={
        200: {"description": "Full content"},
        206: {"description": "Partial content (Range request)"},
        404: {"description": "Document or file not found"},
        416: {"description": "Requested range not satisfiable"},
    },
)
async def get_document_file(
    document_id: int,
    db: DBSession,
    _: Annotated[object, Depends(require_roles("viewer", "editor", "admin"))],
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> StreamingResponse:
    service = DocumentFileService(db)
    stream_result = await service.get_document_file_stream(
        document_id=document_id,
        range_header=range_header,
    )
    return StreamingResponse(
        stream_result.stream,
        status_code=stream_result.status_code,
        headers=stream_result.headers,
        media_type=stream_result.media_type,
    )
