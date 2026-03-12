# Design: Section-by-Section Content Area

**Date:** 2026-03-12
**Status:** Approved

## Problem

The current content area (`TextContent.tsx`) has two modes:

- **View mode:** Renders the entire document as one large `<pre>` block. Active section is highlighted via character offsets.
- **Edit mode:** Renders a flat list of `<textarea>` elements (one per section) via a global `editMode` flag on `ViewPage`.

This approach is being replaced entirely. The new design shows sections as individual cards with inline per-section editing.

## Requirements

- Content area displays sections as a vertical list of cards
- Each section card occupies its natural height (no truncation)
- Each card has an inline Edit button (for `editor`/`admin` roles only)
- TOC click scrolls to the corresponding section card
- Multiple sections can be in edit mode simultaneously
- Each section in edit mode has its own Save and Cancel buttons
- A "Save All" + "Cancel All" button pair appears on the toolbar when any section is dirty
- Only `content` field is editable (heading editing out of scope)

## Architecture

### New Component: `SectionCard.tsx`

Renders a single section node. Props:

```typescript
interface SectionCardProps {
  node: WorkspaceSectionNode
  editValue: string | null       // null = view mode, string = edit mode
  canEdit: boolean
  onEditStart: () => void
  onEditChange: (value: string) => void
  onSave: () => void
  onCancel: () => void
  refCallback: (el: HTMLElement | null) => void
  isActive: boolean              // true when this section is the TOC-selected one
}
```

**View mode** (when `editValue === null`):
- Header bar: heading text (indented by level), suspect badge if `node.is_suspect`, Edit button (pencil icon)
- Body: `<pre>` displaying `node.content`, full height (no truncation)

**Edit mode** (when `editValue !== null`):
- Header bar: heading text, suspect badge (no Edit button)
- Body: `<textarea>` with `editValue`, auto-resize to content
- Footer: Save button + Cancel button

### Refactored `TextContent.tsx`

Becomes a thin container. Props:

```typescript
interface TextContentProps {
  toc: WorkspaceSectionNode[]
  canEdit: boolean
  sectionEdits: Record<number, { content: string }>
  activeSectionId: number | null
  onSectionEditStart: (sectionId: number, currentContent: string) => void
  onSectionEditChange: (sectionId: number, value: string) => void
  onSaveSection: (sectionId: number) => Promise<void>
  onCancelSection: (sectionId: number) => void
}
```

Responsibilities:
- Flatten `toc` with `flattenNodes()` and render one `<SectionCard>` per node
- Maintain `sectionRefs: Map<number, HTMLElement>` via `refCallback`
- `useEffect` on `activeSectionId`: scroll the matching ref into view (`behavior: 'smooth'`)
- Remove all old `fullText`, `activeSection`, `editMode` props

### Refactored `ViewPage.tsx`

State changes:
- Remove `editMode: boolean` state
- Remove `fullText` from workspace state usage (no longer needed in props)
- Keep `sectionEdits: Record<number, { content: string }>` as-is
- `activeSection` remains for TOC selection; pass `activeSection?.section_id` as `activeSectionId` to `TextContent`

Toolbar changes:
- Remove "Chỉnh sửa" / "Lưu thay đổi" / "Hủy" global buttons
- Add "Lưu tất cả" + "Hủy tất cả" buttons, visible only when `Object.keys(sectionEdits).length > 0`

New handler: `handleSaveSection(sectionId: number)`:
- Calls `PATCH /versions/{version_id}/sections/content` with a single item `[{ section_id, content, heading: null }]`
- On success: removes `sectionId` from `sectionEdits`, updates `node.content` in local toc tree (or re-fetches workspace)

Keep existing: `handleSaveSectionEdits` (renamed `handleSaveAll`) for Save All.

## Data Flow

```
ViewPage
  ├── sectionEdits: Record<number, {content: string}>
  ├── activeSection: WorkspaceSectionNode | null
  │
  ├── Toolbar
  │     └── "Lưu tất cả" + "Hủy tất cả" (visible when sectionEdits non-empty)
  │
  └── TextContent
        ├── sectionRefs map → scroll on activeSectionId change
        └── SectionCard[] for each flattened node
              ├── view: <pre> + Edit button
              └── edit: <textarea> + Save + Cancel
```

## UI Sketch

```
┌─────────────────────────────────────────────────┐
│ [Lv1] 1. Đại cương                      [⚠️] [✏️] │
├─────────────────────────────────────────────────┤
│  Nội dung section 1...                          │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│   [Lv2] 1.1 Định nghĩa                      [✏️] │  ← currently being edited
├─────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────┐ │
│ │ textarea (auto-height)                      │ │
│ └─────────────────────────────────────────────┘ │
│                           [Lưu section]  [Hủy]  │
└─────────────────────────────────────────────────┘
```

## CSS

Add classes in `index.css`:
- `.section-card` — card container with border, border-radius, margin-bottom
- `.section-card-header` — flex row, heading text, badge, edit button
- `.section-card-body` — padding, `<pre>` or `<textarea>`
- `.section-card-footer` — flex row end, Save/Cancel buttons (only in edit mode)
- `.section-card--active` — highlight border when TOC-selected
- `.section-card--editing` — visual indicator that section is in edit mode

## Out of Scope

- Heading editing
- Virtual scrolling / windowing
- Optimistic UI for save (re-fetch on success is acceptable)
