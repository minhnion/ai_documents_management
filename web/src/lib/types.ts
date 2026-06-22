// Auth
export interface UserSummaryResponse {
  user_id: number
  email: string
  full_name: string | null
  role: string
  parent_id: number | null
  is_active: boolean
  inherits_global_documents: boolean
}

export interface UserResponse extends UserSummaryResponse {
  parent: UserSummaryResponse | null
  created_by_user_id: number | null
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

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}

export interface ResetUserPasswordRequest {
  new_password: string
}

export interface PasswordActionResponse {
  message: string
}

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
  parent_id?: number | null
  is_active: boolean
  inherits_global_documents: boolean
}

export interface UpdateUserRoleRequest {
  role: string
  parent_id?: number | null
  is_active?: boolean | null
  inherits_global_documents?: boolean | null
}

export interface DeleteUserResponse {
  deleted_user_id: number
  deleted_user_ids: number[]
  deleted_user_count: number
  deleted_guideline_count: number
}

// Guidelines
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
  ten_benh: string | null
  publisher: string | null
  chuyen_khoa: string | null
  owner_user_id: number
  owner: UserSummaryResponse | null
  created_by_user_id: number | null
  active_version: GuidelineVersionSummary | null
  can_edit: boolean
  can_delete: boolean
  access_scope: string
}

export interface GuidelineListResponse {
  items: GuidelineListItem[]
  total: number
  page: number
  page_size: number
}

export interface GuidelineFilterOptionsResponse {
  publishers: string[]
  ten_benhs: string[]
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

export interface VersionIngestionStatusResponse {
  job_id: number | null
  guideline_id: number
  version_id: number
  document_id: number | null
  status: string
  version_status: string | null
  target_status: string | null
  previous_active_versions_updated: number
  error_message: string | null
  requested_at: string | null
  started_at: string | null
  finished_at: string | null
}

// Workspace
export interface WorkspaceGuidelineInfo {
  guideline_id: number
  title: string
  ten_benh: string | null
  publisher: string | null
  chuyen_khoa: string | null
  owner_user_id: number
  owner: UserSummaryResponse | null
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
  owner_user_id: number
  created_by_user_id: number | null
  doc_type: string | null
  storage_uri: string | null
  page_count: number | null
  image_uri: string | null
  pipeline_mode_used: string | null
}

export interface WorkspaceSectionNode {
  section_id: number
  version_id: number
  parent_id: number | null
  heading: string | null
  node_id?: string | null
  section_path: string | null
  level: number | null
  order_index: number | null
  start_char: number | null
  end_char: number | null
  page_start: number | null
  page_end: number | null
  start_y: number | null
  end_y: number | null
  pdf_page_start?: number | null
  pdf_page_end?: number | null
  pdf_start_y?: number | null
  pdf_end_y?: number | null
  score: number | null
  is_suspect: boolean
  content: string | null
  intro_content?: string | null
  heading_bbox?: Record<string, unknown> | null
  content_bboxes?: Record<string, unknown>[]
  landing_chunks?: Record<string, unknown>[]
  children: WorkspaceSectionNode[]
}

export interface VersionWorkspaceResponse {
  guideline: WorkspaceGuidelineInfo
  version: WorkspaceVersionInfo
  documents: WorkspaceDocumentInfo[]
  pipeline_mode_used: string | null
  positioning_mode: string
  toc: WorkspaceSectionNode[]
  section_count: number
  full_text: string | null
  suspect_score_threshold: number
  suspect_section_count: number
  can_edit: boolean
  can_delete: boolean
  access_scope: string
}

// Mutations
export interface CreateGuidelineResponse {
  accepted: boolean
  guideline_id: number
  owner_user_id: number
  version_id: number
  document_id: number
  storage_uri: string | null
  job_id: number | null
  pipeline_status: string
  version_status: string | null
  target_status: string | null
}

export interface CreateGuidelineVersionResponse {
  accepted: boolean
  guideline_id: number
  version_id: number
  status: string | null
  previous_active_versions_updated: number
  document_id: number | null
  storage_uri: string | null
  job_id: number | null
  pipeline_status: string
  version_status: string | null
  target_status: string | null
}

export interface UpdateGuidelineMetadataRequest {
  title?: string | null
  ten_benh?: string | null
  publisher?: string | null
  chuyen_khoa?: string | null
}

export interface UpdateGuidelineMetadataResponse {
  guideline_id: number
  title: string
  ten_benh: string | null
  publisher: string | null
  chuyen_khoa: string | null
  owner_user_id: number
}

export interface UpdateGuidelineVersionMetadataRequest {
  version_label?: string | null
  release_date?: string | null
  status?: string | null
  effective_from?: string | null
  effective_to?: string | null
}

export interface UpdateGuidelineVersionMetadataResponse {
  guideline_id: number
  version_id: number
  version_label: string | null
  release_date: string | null
  status: string | null
  effective_from: string | null
  effective_to: string | null
  promoted_version_id: number | null
  previous_active_versions_updated: number
}

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
}

export interface VersionChunkRebuildStatusResponse {
  job_id: number | null
  version_id: number
  status: string
  deleted_chunk_count: number
  created_chunk_count: number
  error_message: string | null
  requested_at: string | null
  started_at: string | null
  finished_at: string | null
  last_succeeded_at: string | null
}

export interface RebuildVersionChunksResponse extends VersionChunkRebuildStatusResponse {
  accepted: boolean
}
