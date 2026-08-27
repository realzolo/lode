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
- Do not render a search or command-palette control unless the current product
  route provides an implemented searchable command surface.
- Navigation rows are 36px high with a 6px corner radius, 10px horizontal padding, clear hover and focus-visible states, and neutral active fill.
- The signed-in user belongs at the sidebar bottom. Locale, theme, and sign-out live in its menu, not in the top bar.

## Controls

- Inputs, selects, search controls, and standard toolbar buttons share a 36px external height and 6px radius, matching Vercel's compact dashboard controls. A combined search control owns the only border: its nested input has no independent border, padding, or shadow.
- A visible field label is 13px with a 20px line box; its input or select starts 12px below it. Use the shared vertical field layout rather than an unscoped `gap` declaration.
- Toolbar controls use an 8px gap. On narrow screens filter bars wrap into a vertical stack without shrinking important controls below usable width.
- Icon-only buttons require a tooltip and accessible name. Text buttons are reserved for explicit commands such as Save, Retry, or Clear filters.
- All interactive controls expose hover, active, disabled, loading, and `focus-visible` states. Keyboard focus always has a visible ring.

## Operational Surfaces

- Use 1px-bordered, low-radius panels for forms, inspectors, dialogs, and repeated data groups. Do not place decorative cards inside cards.
- Tables and record streams are the default list surface. Wide tables use a horizontal scroll container, stable column widths, truncation for unbounded technical strings, and copy affordances where appropriate.
- Every row that has a detail view is entirely keyboard accessible and has a labelled detail path. A visual arrow may support the affordance but cannot be its only indication.
- Empty, loading, and error states are first-class. Errors expose a Retry action when their source can be fetched again and retain the safe, concrete backend validation reason when one exists. Filtered-empty states preserve filters and offer Clear filters when any filter is active.
- Workflow state is literal: a missing stage is pending only for an active run. A terminal historical run that predates a stage marks it skipped with an explanation; never show it as still queued.

## Status And Feedback

- Use compact 6-8px status chips only for discrete state, with text labels in addition to color. Keep status chips compact rather than converting ordinary metadata into pills.
- Toasts acknowledge completed asynchronous actions. Errors remain visible at the failed surface and must not be toast-only.
- Destructive actions require confirmation where the underlying workflow supports it. Disabled actions explain their unavailable state in nearby text or an accessible label.

## Authenticated UI Checklist

- Reuse `AppShell`, sidebar, topbar, and shared UI primitives.
- Implement only the frozen current API, localization, permission, and business
  contracts; visual alignment must not replace real behavior.
- Verify desktop, tablet, and mobile layouts. Verify mobile navigation, account
  menu, row detail navigation, form submission, localized table/tab scrolling,
  and canonical SSE reload after each shared-shell change.
