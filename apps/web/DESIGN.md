---
version: beta
name: Lode Dashboard System
description: Operational dashboard design language inspired by the structure and interaction quality of Vercel Dashboard. This is a product UI specification, not a marketing-site specification.
---

# Lode Dashboard System

## Product Principles

- The interface is a dense operational workspace for incident investigation,
  Workspace administration, and global model administration. Information
  discovery, status, and safe actions take priority over decoration.
- Use the structure of Vercel Dashboard, not its brand assets, proprietary copy, data, or identity. Lode retains its own product name and domain terms.
- Light and dark themes preserve geometry, hierarchy, keyboard behavior, and status meaning. Theme changes never rearrange information.
- Do not use marketing gradients, hero bands, oversized headlines, illustration cards, or pill-shaped CTAs on authenticated product routes.

## Tokens

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `canvas` | `#ffffff` | `#000000` | App canvas and sidebar |
| `canvas-soft` | `#fafafa` | `#0a0a0a` | Recessed page surface |
| `surface-hover` | `#f1f1f1` | `#1c1c1c` | Row and navigation hover |
| `border` | `rgba(0,0,0,.12)` | `rgba(255,255,255,.16)` | 1px structural border |
| `ink` | `#171717` | `#ededed` | Primary text |
| `muted` | `#6b6b6b` | `#a1a1a1` | Secondary text |
| `focus` | `#0070f3` | `#0070f3` | Keyboard focus ring |
| `success` | `#0070f3` | `#3291ff` | Completed/healthy state |
| `warning` | `#ab570a` | `#f5a623` | Review/attention state |
| `danger` | `#c50000` | `#ee0000` | Failed/destructive state |

Geist is the sans-serif UI face. Geist Mono is reserved for identifiers, branches, hashes, and other technical values. Set `letter-spacing: 0` for all product text. Status color communicates state only; it is not decoration.

## Layout

- Desktop (`>=1024px`): 256px fixed workspace sidebar and 56px context bar. Page content is fluid and uses a maximum readable frame only when a form or prose surface needs it.
- Tablet (`768-1023px`): 64px icon-only sidebar. Navigation labels become accessible names/tooltips, never hidden semantics.
- Mobile (`<768px`): a 56px single-line context bar and an off-canvas navigation drawer. The drawer closes by navigation, backdrop click, and Escape.
- Workspace detail stays inside the global control-plane shell and uses tabs for
  model policy, repositories, Connectors, and ResourceGraph views. Investigation
  routes use the separate Workbench shell.

## Navigation

- The top of the sidebar shows Lode workspace identity and the active product context. Do not render a fake workspace switcher when workspace switching is not a supported Lode capability.
- The sidebar Find control opens the permission-filtered route finder. It is
  available by click and the `F` keyboard shortcut, ignores shortcuts while a
  form control is active, filters immediately, supports arrow-key selection,
  and opens the active match with Enter.
- Navigation rows are 36px high with a 6px corner radius, 10px horizontal padding, clear hover and focus-visible states, and neutral active fill.
- The signed-in user belongs at the sidebar bottom. Locale, theme, and sign-out live in its menu, not in the top bar.

## Controls

- Inputs, selects, search controls, and standard toolbar buttons share a 36px external height and 6px radius, matching Vercel's compact dashboard controls. A combined search control owns the only border: its nested input has no independent border, padding, or shadow.
- Shared text inputs and textareas always opt out of browser and password-manager
  autocomplete. Text inputs load read-only and unlock on the first real user
  focus so saved credentials cannot be injected before interaction; the focus
  transition must preserve normal controlled-input typing and keyboard access.
- A visible field label is 13px with a 20px line box; its input or select starts 12px below it. Use the shared vertical field layout rather than an unscoped `gap` declaration.
- Toolbar controls use an 8px gap. On narrow screens filter bars wrap into a vertical stack without shrinking important controls below usable width.
- Icon-only buttons require a tooltip and accessible name. Text buttons are reserved for explicit commands such as Save, Retry, or Clear filters.
- All interactive controls expose hover, active, disabled, loading, and `focus-visible` states. Keyboard focus always has a visible ring. Async buttons preserve their dimensions, replace the icon/text with a spinner and action label, set `aria-busy`, and reject duplicate submission. Row actions own independent busy state.
- Creation and edit workflows use the right-side `DialogContent` drawer variant at every viewport. The drawer is full-width on phones, constrained on larger screens, scrolls its body independently, and keeps its action footer at the bottom. Destructive confirmations remain centered dialogs.

## Operational Surfaces

- Use 1px-bordered, low-radius panels for forms, inspectors, dialogs, and repeated data groups. Do not place decorative cards inside cards.
- Tables and record streams are the default list surface. Wide tables use a horizontal scroll container, stable column widths, truncation for unbounded technical strings, and copy affordances where appropriate.
- Every row that has a detail view is entirely keyboard accessible and has a labelled detail path. A visual arrow may support the affordance but cannot be its only indication.
- Empty, loading, and error states are first-class. First loads use structural table/list skeletons with the same row height as real data; refresh retains the existing data. Errors expose a Retry action when their source can be fetched again. Client UI renders localized safe messages and keeps stable backend codes out of ordinary display. Filtered-empty states preserve filters and offer Clear filters when any filter is active.
- Workflow state is literal: a missing stage is pending only for an active run. A terminal historical run that predates a stage marks it skipped with an explanation; never show it as still queued.

## Status And Feedback

- Use compact 6-8px status chips only for discrete state, with text labels in addition to color. Keep status chips compact rather than converting ordinary metadata into pills.
- Toasts acknowledge completed asynchronous actions. Errors remain visible at the failed surface and must not be toast-only.
- Destructive actions require confirmation where the underlying workflow supports it. Disabled actions explain their unavailable state in nearby text or an accessible label.

## Internationalization

- Every route and shared component uses `next-intl` for visible labels, placeholders, empty/error/validation text, icon accessible names, and dialog controls. API response messages are not rendered directly.
- Closed enums use explicit typed translation maps. Internal snake_case values, connector capability IDs, role IDs, visibility, readiness, and health states never appear as fallback UI text.
- `en.json` and `zh.json` have identical key sets. `npm run check:i18n` verifies parity and scans TSX for visible string literals. Dates and numbers use the active locale.
- Repository binding uses two dependent `SearchableSelect` controls: choose a
  healthy Git account, then search only that account's available repositories.
  Search lives inside each popover, and changing the account clears the
  repository immediately. No separate Workspace authorization surface exists.
- Workspace Overview groups the Kafka topic and description under one settings
  action and submits both through a single Workspace patch. Architecture context
  remains an immutable revision workflow and uses the distinct command
  `Publish new revision`; do not render a generic Save button beside every field.
- Workspace Members follows the Vercel Members hierarchy: title, description,
  count, right-aligned add action, combined search, permission/status filters,
  compact bordered rows, and row menus. Each row owns its busy/error state;
  removal is confirmed and adding uses a right-side drawer with a searchable
  active-user combobox.

## Authenticated UI Checklist

- Reuse `AppShell`, sidebar, topbar, and shared UI primitives.
- Keep shared Vercel Dashboard shell/surface rules in `app/dashboard.css`, loaded
  after `globals.css`; keep investigation-domain visualizations in
  `globals.css` rather than coupling them to the shell layer.
- Implement only the frozen current API, localization, permission, and business
  contracts; visual alignment must not replace real behavior.
- Verify desktop, tablet, and mobile layouts. Verify mobile navigation, account
  menu, row detail navigation, form submission, localized table/tab scrolling,
  and canonical SSE reload after each shared-shell change.
