# Section-by-Section Content Area Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current single-`<pre>` view mode and flat-textarea edit mode with a section-card list where each section is shown independently at full height with its own inline Edit/Save/Cancel controls.

**Architecture:** Create `SectionCard.tsx` (renders one section in view or edit mode), refactor `TextContent.tsx` into a thin container that maps sections to cards and handles TOC scroll-to, and remove global `editMode` state from `ViewPage.tsx`.

**Tech Stack:** React 19, TypeScript, lucide-react, custom CSS (index.css). No new dependencies.

---

## Task 1: Create `SectionCard.tsx` component

**Files:**
- Create: `web/src/components/SectionCard.tsx`

### Step 1: Create the SectionCard component

```tsx
// web/src/components/SectionCard.tsx
import { useEffect, useRef } from 'react'
import { Edit3, Check, X, AlertTriangle } from 'lucide-react'
import type { WorkspaceSectionNode } from '../lib/types'

interface SectionCardProps {
  node: WorkspaceSectionNode
  editValue: string | null        // null = view mode, string = edit mode
  canEdit: boolean
  isActive: boolean               // true when TOC-selected
  refCallback: (el: HTMLDivElement | null) => void
  onEditStart: () => void
  onEditChange: (value: string) => void
  onSave: () => void
  onCancel: () => void
  saving?: boolean
}

export default function SectionCard({
  node,
  editValue,
  canEdit,
  isActive,
  refCallback,
  onEditStart,
  onEditChange,
  onSave,
  onCancel,
  saving = false,
}: SectionCardProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-focus textarea when entering edit mode
  useEffect(() => {
    if (editValue !== null && textareaRef.current) {
      textareaRef.current.focus()
    }
  }, [editValue !== null]) // only when entering/leaving edit

  // Auto-resize textarea height to content
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px'
    }
  }, [editValue])

  const headingLabel = node.heading || `Mục ${node.section_id}`
  const indent = Math.max(0, (node.level ?? 1) - 1) * 16

  const isEditing = editValue !== null

  return (
    <div
      ref={refCallback}
      className={`section-card${isActive ? ' section-card--active' : ''}${isEditing ? ' section-card--editing' : ''}`}
    >
      {/* Header */}
      <div className="section-card-header" style={{ paddingLeft: indent }}>
        <span className={`section-card-heading section-heading-level-${node.level ?? 1}`}>
          {headingLabel}
        </span>
        <div className="section-card-header-actions">
          {node.is_suspect && (
            <span className="section-suspect-badge" title="Mục cần kiểm tra chất lượng OCR">
              <AlertTriangle size={13} />
            </span>
          )}
          {canEdit && !isEditing && (
            <button
              className="btn btn-ghost btn-xs section-edit-btn"
              onClick={onEditStart}
              title="Chỉnh sửa mục này"
            >
              <Edit3 size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="section-card-body">
        {isEditing ? (
          <textarea
            ref={textareaRef}
            className="section-card-textarea"
            value={editValue}
            onChange={e => onEditChange(e.target.value)}
            disabled={saving}
          />
        ) : (
          <pre className="section-card-content">
            {node.content || <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>Không có nội dung.</span>}
          </pre>
        )}
      </div>

      {/* Footer (edit mode only) */}
      {isEditing && (
        <div className="section-card-footer">
          <button
            className="btn btn-primary btn-xs"
            onClick={onSave}
            disabled={saving}
          >
            {saving
              ? <span className="loading-spinner" style={{ width: 12, height: 12 }} />
              : <><Check size={12} /> Lưu</>
            }
          </button>
          <button
            className="btn btn-secondary btn-xs"
            onClick={onCancel}
            disabled={saving}
          >
            <X size={12} /> Hủy
          </button>
        </div>
      )}
    </div>
  )
}
```

### Step 2: Verify the file was created correctly

```bash
cat web/src/components/SectionCard.tsx | head -5
```

Expected output: first lines of the new file.

### Step 3: Commit

```bash
git add web/src/components/SectionCard.tsx
git commit -m "feat: add SectionCard component for per-section view/edit"
```

---

## Task 2: Add CSS for SectionCard in `index.css`

**Files:**
- Modify: `web/src/index.css` (append after `.content-highlight` block, around line 745)

### Step 1: Add section-card CSS classes

Find the `.content-highlight` block (ends around line 745) and add the following CSS **after** it:

```css
/* ── Section Cards (content area) ──────────────────────────────── */
.section-card {
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--bg-surface);
  margin-bottom: 16px;
  transition: border-color var(--t), box-shadow var(--t);
}

.section-card--active {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-light);
}

.section-card--editing {
  border-color: var(--accent);
}

.section-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  border-radius: var(--r-md) var(--r-md) 0 0;
}

.section-card--editing .section-card-header {
  background: var(--accent-light);
  border-bottom-color: var(--accent);
}

.section-card-heading {
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
  min-width: 0;
  word-break: break-word;
}

.section-heading-level-1 { font-size: 16px; }
.section-heading-level-2 { font-size: 15px; }
.section-heading-level-3 { font-size: 14px; font-weight: 500; }
.section-heading-level-4,
.section-heading-level-5,
.section-heading-level-6 { font-size: 13px; font-weight: 500; color: var(--text-secondary); }

.section-card-header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.section-suspect-badge {
  display: flex;
  align-items: center;
  color: var(--warning);
}

.section-edit-btn {
  opacity: 0;
  transition: opacity var(--t);
}

.section-card:hover .section-edit-btn {
  opacity: 1;
}

.section-card-body {
  padding: 16px 20px;
}

.section-card-content {
  font-size: 15px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
  font-family: inherit;
  margin: 0;
}

.section-card-textarea {
  width: 100%;
  min-height: 120px;
  padding: 10px 12px;
  font-size: 15px;
  font-family: inherit;
  line-height: 1.8;
  color: var(--text-primary);
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  resize: none;
  overflow: hidden;
  transition: border-color var(--t);
  outline: none;
}

.section-card-textarea:focus {
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px var(--accent-light);
}

.section-card-textarea:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.section-card-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--border);
  background: var(--bg);
  border-radius: 0 0 var(--r-md) var(--r-md);
}
```

### Step 2: Commit

```bash
git add web/src/index.css
git commit -m "style: add section-card CSS classes for per-section layout"
```

---

## Task 3: Refactor `TextContent.tsx`

**Files:**
- Modify: `web/src/components/TextContent.tsx` (full rewrite)

### Step 1: Replace the entire file content

The new `TextContent.tsx` is a thin container. It:
- Accepts the new props interface (no `fullText`, no `editMode`)
- Maintains a `Map<number, HTMLDivElement>` of refs for each section card
- `useEffect` scrolls to `activeSectionId` when it changes
- Renders `flattenNodes(toc)` as `<SectionCard>` components

```tsx
// web/src/components/TextContent.tsx
import { useEffect, useRef, useCallback } from 'react'
import { type WorkspaceSectionNode } from '../lib/types'
import SectionCard from './SectionCard'

interface TextContentProps {
  toc: WorkspaceSectionNode[]
  canEdit: boolean
  activeSectionId: number | null
  sectionEdits: Record<number, { content: string }>
  savingSections: Record<number, boolean>
  onSectionEditStart: (sectionId: number, currentContent: string) => void
  onSectionEditChange: (sectionId: number, value: string) => void
  onSaveSection: (sectionId: number) => Promise<void>
  onCancelSection: (sectionId: number) => void
}

function flattenNodes(nodes: WorkspaceSectionNode[]): WorkspaceSectionNode[] {
  const result: WorkspaceSectionNode[] = []
  for (const node of nodes) {
    result.push(node)
    if (node.children.length > 0) {
      result.push(...flattenNodes(node.children))
    }
  }
  return result
}

export default function TextContent({
  toc,
  canEdit,
  activeSectionId,
  sectionEdits,
  savingSections,
  onSectionEditStart,
  onSectionEditChange,
  onSaveSection,
  onCancelSection,
}: TextContentProps) {
  const sectionRefs = useRef<Map<number, HTMLDivElement>>(new Map())

  // Scroll to active section when TOC selection changes
  useEffect(() => {
    if (activeSectionId == null) return
    const el = sectionRefs.current.get(activeSectionId)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [activeSectionId])

  const setRef = useCallback((sectionId: number) => (el: HTMLDivElement | null) => {
    if (el) {
      sectionRefs.current.set(sectionId, el)
    } else {
      sectionRefs.current.delete(sectionId)
    }
  }, [])

  if (!toc || toc.length === 0) {
    return (
      <div className="loading-center">
        <span className="text-muted">Không có nội dung văn bản.</span>
      </div>
    )
  }

  const flatNodes = flattenNodes(toc)

  return (
    <div className="content-body">
      {flatNodes.map(node => (
        <SectionCard
          key={node.section_id}
          node={node}
          editValue={
            node.section_id in sectionEdits
              ? sectionEdits[node.section_id].content
              : null
          }
          canEdit={canEdit}
          isActive={node.section_id === activeSectionId}
          refCallback={setRef(node.section_id)}
          saving={savingSections[node.section_id] ?? false}
          onEditStart={() => onSectionEditStart(node.section_id, node.content ?? '')}
          onEditChange={value => onSectionEditChange(node.section_id, value)}
          onSave={() => onSaveSection(node.section_id)}
          onCancel={() => onCancelSection(node.section_id)}
        />
      ))}
    </div>
  )
}
```

### Step 2: Commit

```bash
git add web/src/components/TextContent.tsx
git commit -m "refactor: rewrite TextContent as section-card container"
```

---

## Task 4: Refactor `ViewPage.tsx`

**Files:**
- Modify: `web/src/pages/ViewPage.tsx`

### Step 1: Remove old state and add new state

**State to remove:**
- `editMode: boolean` — global edit toggle no longer needed

**State to add:**
- `savingSections: Record<number, boolean>` — tracks per-section save in-progress state

**Type change for `sectionEdits`:**
- Old: `Record<number, { content: string | null; heading: string | null }>`
- New: `Record<number, { content: string }>`

### Step 2: Replace handlers

**Remove:** `handleSaveSectionEdits` (it both set `editMode(false)` and saved)

**Add:**
```typescript
// Save a single section
const handleSaveSection = async (sectionId: number) => {
  if (!workspace) return
  setSavingSections(prev => ({ ...prev, [sectionId]: true }))
  setSaveError('')
  try {
    await api.patch(`/versions/${workspace.version.version_id}/sections/content`, {
      updates: [{
        section_id: sectionId,
        content: sectionEdits[sectionId]?.content ?? null,
        heading: null,
      }]
    })
    // Re-fetch workspace to sync updated content
    const wsRes = await api.get<VersionWorkspaceResponse>(`/versions/${workspace.version.version_id}/workspace`)
    setWorkspace(wsRes.data)
    setSectionEdits(prev => {
      const next = { ...prev }
      delete next[sectionId]
      return next
    })
  } catch (err: any) {
    setSaveError(err.response?.data?.detail || 'Lỗi khi lưu nội dung.')
  } finally {
    setSavingSections(prev => {
      const next = { ...prev }
      delete next[sectionId]
      return next
    })
  }
}

// Save all dirty sections at once
const handleSaveAll = async () => {
  if (!workspace) return
  const updates = Object.entries(sectionEdits).map(([id, val]) => ({
    section_id: Number(id),
    content: val.content,
    heading: null,
  }))
  if (updates.length === 0) return
  setSaving(true)
  setSaveError('')
  try {
    await api.patch(`/versions/${workspace.version.version_id}/sections/content`, { updates })
    const wsRes = await api.get<VersionWorkspaceResponse>(`/versions/${workspace.version.version_id}/workspace`)
    setWorkspace(wsRes.data)
    setSectionEdits({})
  } catch (err: any) {
    setSaveError(err.response?.data?.detail || 'Lỗi khi lưu nội dung.')
  } finally {
    setSaving(false)
  }
}

// Edit start: add section to sectionEdits
const handleSectionEditStart = (sectionId: number, currentContent: string) => {
  setSectionEdits(prev => ({
    ...prev,
    [sectionId]: { content: currentContent },
  }))
}

// Edit change: update content in sectionEdits
const handleSectionEditChange = (sectionId: number, value: string) => {
  setSectionEdits(prev => ({
    ...prev,
    [sectionId]: { content: value },
  }))
}

// Cancel: remove section from sectionEdits
const handleCancelSection = (sectionId: number) => {
  setSectionEdits(prev => {
    const next = { ...prev }
    delete next[sectionId]
    return next
  })
  setSaveError('')
}
```

### Step 3: Update the toolbar JSX

**Remove** these buttons from the toolbar:
```tsx
{canEdit && !editMode && (
  <button className="btn btn-secondary btn-xs" onClick={() => setEditMode(true)}>
    <Edit3 size={12} /> Chỉnh sửa
  </button>
)}
{editMode && (
  <>
    <button ... onClick={handleSaveSectionEdits}>Lưu thay đổi</button>
    <button ... onClick={() => { setEditMode(false); setSectionEdits({}); setSaveError('') }}>Hủy</button>
  </>
)}
```

**Add** these buttons instead:
```tsx
{Object.keys(sectionEdits).length > 0 && (
  <>
    <button
      className="btn btn-primary btn-xs"
      disabled={saving}
      onClick={handleSaveAll}
    >
      {saving
        ? <span className="loading-spinner" style={{ width: 12, height: 12 }} />
        : <><Check size={12} /> Lưu tất cả ({Object.keys(sectionEdits).length})</>
      }
    </button>
    <button
      className="btn btn-secondary btn-xs"
      disabled={saving}
      onClick={() => { setSectionEdits({}); setSaveError('') }}
    >
      <X size={12} /> Hủy tất cả
    </button>
  </>
)}
```

### Step 4: Update the `<TextContent>` JSX

**Remove** old props: `fullText`, `activeSection`, `editMode`, `onSectionEdit`

**Replace** the TextContent usage with:
```tsx
<TextContent
  toc={workspace.toc}
  canEdit={canEdit}
  activeSectionId={activeSection?.section_id ?? null}
  sectionEdits={sectionEdits}
  savingSections={savingSections}
  onSectionEditStart={handleSectionEditStart}
  onSectionEditChange={handleSectionEditChange}
  onSaveSection={handleSaveSection}
  onCancelSection={handleCancelSection}
/>
```

### Step 5: Remove unused imports

Remove from the import line:
- `Edit3` (no longer used in ViewPage toolbar — it's now in SectionCard)
- `useState` import may still be needed; keep it

Check: `Edit3` is used in SectionCard, not ViewPage. Remove it from ViewPage import.

### Step 6: Commit

```bash
git add web/src/pages/ViewPage.tsx
git commit -m "refactor: remove global editMode, add per-section save in ViewPage"
```

---

## Task 5: Verify the app builds and works

### Step 1: Run TypeScript type check

```bash
cd web && npx tsc --noEmit
```

Expected: no errors (or only pre-existing unrelated errors).

### Step 2: Run the dev server and manually test

```bash
cd web && npm run dev
```

Manual test checklist:
1. Open a guideline → ViewPage loads, sections appear as cards
2. Click a TOC item → content area scrolls to that section card, card gets blue border highlight
3. Hover over a section card → Edit button (pencil) appears in header
4. Click Edit button → card switches to textarea mode with Save/Cancel buttons
5. Edit text in textarea → textarea auto-resizes
6. Click Cancel → card returns to view mode, original content restored
7. Edit two sections simultaneously → both are in edit mode, toolbar shows "Lưu tất cả (2)"
8. Click "Lưu section" on one → only that section is saved, the other remains in edit mode
9. Click "Lưu tất cả" → all dirty sections saved, toolbar buttons disappear
10. Suspect sections show warning triangle badge in header
11. Viewer role (non-editor) → no Edit buttons visible anywhere

### Step 3: Commit if any fixes needed

```bash
git add -A
git commit -m "fix: correct any TypeScript or runtime issues in section-card refactor"
```

---

## Task 6: Clean up unused CSS

**Files:**
- Modify: `web/src/index.css`

### Step 1: Remove now-unused CSS classes

The following CSS classes are no longer used after this refactor:
- `.content-text` — was used by the `<pre>` in old view mode
- `.content-highlight` — was used for TOC highlight in old view mode

Check that these classes are truly unused:

```bash
grep -r "content-text\|content-highlight" web/src/
```

Expected: no matches in `.tsx` or `.ts` files (only the definitions in `index.css`).

If confirmed unused, remove the two blocks from `index.css`:
```css
.content-text { ... }
.content-highlight { ... }
```

### Step 2: Commit

```bash
git add web/src/index.css
git commit -m "style: remove unused content-text and content-highlight CSS after refactor"
```

---

## Summary of Changes

| File | Action |
|------|--------|
| `web/src/components/SectionCard.tsx` | **Create** — new per-section card component |
| `web/src/index.css` | **Modify** — add section-card CSS, remove old content-text/highlight CSS |
| `web/src/components/TextContent.tsx` | **Rewrite** — thin container using SectionCard, scroll-to logic |
| `web/src/pages/ViewPage.tsx` | **Modify** — remove editMode state, add per-section and save-all handlers |
