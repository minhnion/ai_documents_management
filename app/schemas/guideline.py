from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class GuidelineVersionSummary(BaseModel):
    version_id: int
    version_label: str | None = None
    status: str | None = None
    release_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class GuidelineListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guideline_id: int
    title: str
    ten_benh: str | None = None
    publisher: str | None = None
    chuyen_khoa: str | None = None
    active_version: GuidelineVersionSummary | None = None


class GuidelineListResponse(BaseModel):
    items: list[GuidelineListItem]
    total: int
    page: int
    page_size: int


class GuidelineVersionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_id: int
    guideline_id: int
    version_label: str | None = None
    release_date: date | None = None
    status: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class GuidelineVersionListResponse(BaseModel):
    guideline_id: int
    items: list[GuidelineVersionItem]
    total: int
    page: int
    page_size: int


class VersionIngestionStatusResponse(BaseModel):
    job_id: int | None = None
    guideline_id: int
    version_id: int
    document_id: int | None = None
    status: str
    version_status: str | None = None
    target_status: str | None = None
    previous_active_versions_updated: int = 0
    error_message: str | None = None
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CreateGuidelineResponse(BaseModel):
    accepted: bool
    guideline_id: int
    version_id: int
    document_id: int
    storage_uri: str | None = None
    job_id: int | None = None
    pipeline_status: str
    version_status: str | None = None
    target_status: str | None = None


class CreateGuidelineVersionResponse(BaseModel):
    accepted: bool
    guideline_id: int
    version_id: int
    status: str | None = None
    previous_active_versions_updated: int = 0
    document_id: int | None = None
    storage_uri: str | None = None
    job_id: int | None = None
    pipeline_status: str
    version_status: str | None = None
    target_status: str | None = None


class DeleteGuidelineResponse(BaseModel):
    guideline_id: int
    deleted_version_count: int


class DeleteGuidelineVersionResponse(BaseModel):
    guideline_id: int
    deleted_version_id: int
    promoted_version_id: int | None = None
    remaining_version_count: int


class VersionChunkRebuildStatusResponse(BaseModel):
    job_id: int | None = None
    version_id: int
    status: str
    deleted_chunk_count: int = 0
    created_chunk_count: int = 0
    error_message: str | None = None
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RebuildVersionChunksResponse(VersionChunkRebuildStatusResponse):
    accepted: bool


class WorkspaceGuidelineInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guideline_id: int
    title: str
    ten_benh: str | None = None
    publisher: str | None = None
    chuyen_khoa: str | None = None


class WorkspaceVersionInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_id: int
    guideline_id: int
    version_label: str | None = None
    release_date: date | None = None
    status: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class WorkspaceDocumentInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: int
    version_id: int
    doc_type: str | None = None
    storage_uri: str | None = None
    page_count: int | None = None
    image_uri: str | None = None


class WorkspaceSectionNode(BaseModel):
    section_id: int
    version_id: int
    parent_id: int | None = None
    heading: str | None = None
    section_path: str | None = None
    level: int | None = None
    order_index: int | None = None
    start_char: int | None = None
    end_char: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    score: float | None = None
    is_suspect: bool = False
    content: str | None = None
    children: list['WorkspaceSectionNode'] = Field(default_factory=list)


WorkspaceSectionNode.model_rebuild()


class VersionWorkspaceResponse(BaseModel):
    guideline: WorkspaceGuidelineInfo
    version: WorkspaceVersionInfo
    documents: list[WorkspaceDocumentInfo]
    toc: list[WorkspaceSectionNode]
    section_count: int
    suspect_score_threshold: float
    suspect_section_count: int
    full_text: str | None = None


class SectionContentUpdateItem(BaseModel):
    section_id: int = Field(gt=0)
    content: str | None = None
    heading: str | None = None


class BulkSectionContentUpdateRequest(BaseModel):
    updates: list[SectionContentUpdateItem] = Field(min_length=1)


class BulkSectionContentUpdateResponse(BaseModel):
    version_id: int
    requested_count: int
    updated_count: int
    updated_section_ids: list[int]


class GuidelineFilterOptionsResponse(BaseModel):
    publishers: list[str]
    ten_benhs: list[str]
