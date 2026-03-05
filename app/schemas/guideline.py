from datetime import date

from pydantic import BaseModel, ConfigDict


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
