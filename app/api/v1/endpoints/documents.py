from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from app.api.deps import ActiveUser, DBSession
from app.services.document_file_service import DocumentFileService
from app.services.tenant_access_service import TenantAccessService

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
    current_user: ActiveUser,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> StreamingResponse:
    await TenantAccessService(db).ensure_document_access(
        document_id=document_id,
        current_user=current_user,
    )
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
