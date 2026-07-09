export const ROLE_ADMIN = 'admin'
export const ROLE_HEALTH_DEPARTMENT = 'health_department'
export const ROLE_CENTRAL_HOSPITAL = 'central_hospital'
export const ROLE_HOSPITAL = 'hospital'
export const ROLE_HEALTH_STATION = 'health_station'
export const ROLE_DOCTOR = 'doctor'

export const DOCUMENT_MANAGER_ROLES = [
  ROLE_ADMIN,
  ROLE_HEALTH_DEPARTMENT,
  ROLE_CENTRAL_HOSPITAL,
  ROLE_HOSPITAL,
  ROLE_HEALTH_STATION,
] as const

export const ACCOUNT_MANAGER_ROLES = DOCUMENT_MANAGER_ROLES

export const TOP_LEVEL_UNIT_ROLES = [
  ROLE_HEALTH_DEPARTMENT,
  ROLE_CENTRAL_HOSPITAL,
] as const

export const HEALTH_STATION_SPECIALTY = 'Trạm y tế'

const ROLE_LABELS: Record<string, string> = {
  [ROLE_ADMIN]: 'Admin',
  [ROLE_HEALTH_DEPARTMENT]: 'Sở y tế',
  [ROLE_CENTRAL_HOSPITAL]: 'Bệnh viện Trung ương',
  [ROLE_HOSPITAL]: 'Bệnh viện',
  [ROLE_HEALTH_STATION]: 'Trạm y tế',
  [ROLE_DOCTOR]: 'Bác sĩ',
}

export function roleLabel(role: string) {
  return ROLE_LABELS[role] ?? role
}

export function isDocumentManagerRole(role: string | null | undefined) {
  return DOCUMENT_MANAGER_ROLES.includes(role as typeof DOCUMENT_MANAGER_ROLES[number])
}

export function isAccountManagerRole(role: string | null | undefined) {
  return ACCOUNT_MANAGER_ROLES.includes(role as typeof ACCOUNT_MANAGER_ROLES[number])
}

export function isTopLevelUnitRole(role: string | null | undefined) {
  return TOP_LEVEL_UNIT_ROLES.includes(role as typeof TOP_LEVEL_UNIT_ROLES[number])
}
