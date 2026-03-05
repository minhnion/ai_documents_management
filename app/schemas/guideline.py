from datetime import date

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


class CreateGuidelineResponse(BaseModel):
    guideline_id: int
    version_id: int
    document_id: int
    storage_uri: str | None = None


class WorkspaceGuidelineInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    guideline_id: int
    title: str
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
    content: str | None = None
    children: list["WorkspaceSectionNode"] = Field(default_factory=list)


WorkspaceSectionNode.model_rebuild()


class VersionWorkspaceResponse(BaseModel):
    guideline: WorkspaceGuidelineInfo
    version: WorkspaceVersionInfo
    documents: list[WorkspaceDocumentInfo]
    toc: list[WorkspaceSectionNode]
    section_count: int
    full_text: str | None = None
