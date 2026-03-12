# Design: Version Manager Popup in ListPage

**Date:** 2026-03-12  
**Status:** Approved

## Summary

Remove the trash button from ViewPage's version bar. Instead, add a "Quản lý phiên bản" button to the action column in ListPage. Clicking it opens a modal popup showing all versions of that guideline with per-version delete capability.

## Changes

### 1. ViewPage.tsx — Remove trash button

- Delete the `handleDeleteVersion` function (lines 51–69)
- Remove all related state: `deletingVersion`, `setDeletingVersion`
- Remove the trash button JSX (lines 179–188) from the `.version-bar` div
- Remove unused import: `Trash2` from lucide-react (if no longer used elsewhere)

### 2. ListPage.tsx — Add version manager button

- Add state: `versionModalGuidelineId: string | null` and `versionModalGuidelineTitle: string`
- Add a "Phiên bản" button (icon: `Layers`) in the `.actions-cell` per row
  - Visible to users with `canEdit` role (editor or admin) — same as trash in ViewPage
  - On click: set `versionModalGuidelineId` and `versionModalGuidelineTitle`, opening the modal
- Render `<VersionManagerModal>` when `versionModalGuidelineId` is not null
- On modal close or after last version deleted: clear state + reload guideline list

### 3. New component: `web/src/components/VersionManagerModal.tsx`

**Props:**
```ts
interface Props {
  guidelineId: string;
  guidelineTitle: string;
  onClose: () => void;
  onVersionsChanged: () => void; // triggers list reload when versions are deleted
}
```

**Behavior:**
- On mount: `GET /guidelines/:guidelineId/versions` → display version list
- Table columns: Phiên bản, Ngày phát hành, Trạng thái, Thao tác
- Delete button per row: `window.confirm()` → `DELETE /versions/:versionId` → refresh list
- If list becomes empty after delete: call `onVersionsChanged()` then `onClose()`
- Loading spinner during fetch and delete operations
- Error shown inline (not alert) if delete fails

**Structure:**
```
.modal-overlay        — full-screen dark backdrop, click to close
  .modal-container    — centered white box
    .modal-header     — title + close (×) button
    .modal-body       — versions table or empty state
    .modal-footer     — Đóng button
```

### 4. index.css — Modal styles

Add styles for: `.modal-overlay`, `.modal-container`, `.modal-header`, `.modal-body`, `.modal-footer`, `.modal-close-btn`

## API Used

- `GET /guidelines/{guideline_id}/versions` — list versions (already exists)
- `DELETE /versions/{version_id}` — delete version (already exists, currently used in ViewPage)

## Role / Access

- "Quản lý phiên bản" button visible only when `user?.role === 'editor' || user?.role === 'admin'`
- Delete button within modal visible to same roles

## Non-Goals

- No create-new-version inside the modal (use UpdatePage for that)
- No navigation to ViewPage from inside the modal
