// ── Auth ──────────────────────────────────────────────────────────
export interface UserResponse {
  user_id: number
  email: string
  full_name: string | null
  role: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface LoginResponse {
  access_token: string
  expires_in: number
  user: UserResponse
}

// ── Guidelines ────────────────────────────────────────────────────
export interface GuidelineVersionSummary {
  version_id: number
  version_label: string | null
  status: string | null
  release_date: string | null
  effective_from: string | null
  effective_to: string | null
}

export interface GuidelineListItem {
  guideline_id: number
  title: string
  publisher: string | null
  chuyen_khoa: string | null
  active_version: GuidelineVersionSummary | null
}

export interface GuidelineListResponse {
  items: GuidelineListItem[]
  total: number
  page: number
  page_size: number
}

export interface GuidelineVersionItem {
  version_id: number
  guideline_id: number
  version_label: string | null
  release_date: string | null
  status: string | null
  effective_from: string | null
  effective_to: string | null
}

export interface GuidelineVersionListResponse {
  guideline_id: number
  items: GuidelineVersionItem[]
  total: number
  page: number
  page_size: number
}

// ── Workspace ─────────────────────────────────────────────────────
export interface WorkspaceGuidelineInfo {
  guideline_id: number
  title: string
  publisher: string | null
  chuyen_khoa: string | null
}

export interface WorkspaceVersionInfo {
  version_id: number
  guideline_id: number
  version_label: string | null
  release_date: string | null
  status: string | null
  effective_from: string | null
  effective_to: string | null
}

export interface WorkspaceDocumentInfo {
  document_id: number
  version_id: number
  doc_type: string | null
  storage_uri: string | null
  page_count: number | null
  image_uri: string | null
}

export interface WorkspaceSectionNode {
  section_id: number
  version_id: number
  parent_id: number | null
  heading: string | null
  section_path: string | null
  level: number | null
  order_index: number | null
  start_char: number | null
  end_char: number | null
  page_start: number | null
  page_end: number | null
  content: string | null
  children: WorkspaceSectionNode[]
  score: number | null
  is_suspect: boolean
}

export interface VersionWorkspaceResponse {
  guideline: WorkspaceGuidelineInfo
  version: WorkspaceVersionInfo
  documents: WorkspaceDocumentInfo[]
  toc: WorkspaceSectionNode[]
  section_count: number
  full_text: string | null
  suspect_score_threshold: number
  suspect_section_count: number
}

// ── Mutations ─────────────────────────────────────────────────────
export interface CreateGuidelineResponse {
  guideline_id: number
  version_id: number
  document_id: number
  storage_uri: string | null
}

export interface CreateGuidelineVersionResponse {
  guideline_id: number
  version_id: number
  status: string | null
  previous_active_versions_updated: number
  document_id: number | null
  storage_uri: string | null
}

// ── Admin ─────────────────────────────────────────────────────────
export interface AvailableRoleResponse {
  name: string
  description: string
}

export interface UserListResponse {
  items: UserResponse[]
  total: number
}

export interface CreateUserRequest {
  email: string
  full_name: string | null
  password: string
  role: string
  is_active: boolean
}

export interface UpdateUserRoleRequest {
  role: string
}

// ── Delete responses ───────────────────────────────────────────────
export interface DeleteGuidelineResponse {
  guideline_id: number
  deleted_version_count: number
}

export interface DeleteGuidelineVersionResponse {
  guideline_id: number
  deleted_version_id: number
  promoted_version_id: number | null
  remaining_version_count: number
}

// ── Bulk section update ────────────────────────────────────────────
export interface SectionContentUpdateItem {
  section_id: number
  content: string | null
  heading: string | null
}

export interface BulkSectionContentUpdateRequest {
  updates: SectionContentUpdateItem[]
}

export interface BulkSectionContentUpdateResponse {
  version_id: number
  requested_count: number
  updated_count: number
  updated_section_ids: number[]
  deleted_chunk_count: number
  created_chunk_count: number
}
