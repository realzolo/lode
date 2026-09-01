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

## Brand Identity

- The canonical mark is the Bounded Scope symbol documented in
  `docs/brand/README.md`: paired analytical boundaries around one structured
  Evidence Blue input.
- Product UI renders the theme-aware React mark from
  `components/brand/lode-logo.tsx`; exported material uses the SVG assets under
  `public/brand`.
- The mark is never recolored with operational status colors, placed in a badge,
  shadowed, or substituted with the previous triangle placeholder.
- Authentication surfaces use a quiet full-height split frame: an abstract
  Bounded Scope analysis field, an unframed form workspace, structural
  hairlines, and no marketing content. Narrow screens collapse to one column.

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
- Mobile (`<768px`): a 56px single-line context bar and an off-canvas navigation drawer. The drawer closes by navigation, backdrop click, and Escape; it locks page scroll, traps keyboard focus while open, restores focus to its trigger on close, and is removed from visual and keyboard navigation while closed.
- Workspace detail stays inside the global control-plane shell and uses 50px
  underline tabs with one shared hairline baseline for model policy,
  repositories, Connectors, and ResourceGraph views. Investigation routes use
  the separate Workbench shell.

## Navigation

- The top of the sidebar shows Lode workspace identity and the active product context. Do not render a fake workspace switcher when workspace switching is not a supported Lode capability.
- The sidebar Find control opens the permission-filtered route finder. It is
  available by click and the `Cmd/Ctrl+K` keyboard shortcut, ignores shortcuts
  while a form control is active, filters immediately, supports arrow-key
  selection, and opens the active match with Enter. Opening Finder dismisses
  the mobile navigation drawer so only one modal navigation layer is active.
- Navigation rows are 36px high with a 6px corner radius, 10px horizontal padding, clear hover and focus-visible states, and neutral active fill. Finder is a 12px command panel; its input retains keyboard focus while arrow keys expose the active match through standard combobox/listbox semantics. Result rows are pointer-selectable rather than Tab stops, and an off-screen live result count announces filtering changes.
- The signed-in user belongs at the sidebar bottom. Locale, theme, and sign-out live in its menu, not in the top bar. The menu exposes explicit Chinese / English locale selections and one global Light / System / Dark theme selection, with the current value marked in each group.

## Controls

- Inputs, selects, search controls, and standard toolbar buttons share a 36px external height and 6px radius, matching Vercel's compact dashboard controls. A combined search control owns the only border: its nested input has no independent border, padding, or shadow. Every standalone input has a visible label or an accessible name; placeholders are examples, never the sole label. Use a non-nested labelled group around Radix button-based Select controls. Invalid inputs, textareas, selects, and token-entry controls use the same red border and red focus ring while their field-level error copy describes the issue.
- Checkboxes use the shared native-input wrapper with a 16px square, 4px radius,
  explicit checked indicator, focus ring, and disabled treatment. Do not render
  browser-default checkboxes inside dashboard controls, tables, or drawers.
- Use a Toggle for one independent boolean configuration, a Switch for a
  two- or three-option mutually exclusive view, and a Checkbox for selecting one
  or more items from a set. Toggles keep a native focusable input and require an
  accessible name when no external visible label is associated. A disabled
  Toggle names the resolution path in one sentence of helper text.
- Shared text inputs, textareas, and combobox search inputs always opt out of
  browser and password-manager autocomplete, auto-correction, auto-capitalization,
  and spellchecking, and remain editable by default. Use explicit read-only
  semantics only when the product state requires them; normal controlled-input
  typing and keyboard access must remain intact.
- A visible field label is 13px with a 20px line box; its input or select starts 12px below it. Use the shared vertical field layout rather than an unscoped `gap` declaration.
- Toolbar controls use an 8px gap. Keep primary search and one or two high-frequency scopes visible; put secondary list conditions in a compact Popover reached by a labelled Filters button with an active-count indicator. A filter Popover is bounded by Radix's available viewport height and scrolls internally when needed. On narrow screens filter bars wrap into a vertical stack without shrinking important controls below usable width.
- A compact mode switch is a two- or three-option segmented control: it keeps a neutral shared outline, uses a subtly elevated selected surface instead of a primary CTA fill, and exposes selection with `aria-pressed`.
- Select, combobox, and account-menu surfaces use a 12px radius. Combobox
  triggers declare their controlled listbox, expanded state, and busy state so
  keyboard and assistive-technology selection follows the visible surface. Larger modal,
  command, and toast surfaces use 12px so hierarchy comes from the interaction
  layer rather than decorative cards.
- Icon-only buttons require the shared Radix Tooltip and an accessible name. It opens on hover and keyboard focus after 150ms, closes with Escape, and uses the 6px tooltip material with a triangular stem. Text buttons are reserved for explicit commands such as Save, Retry, or Clear filters.
- All interactive controls expose hover, active, disabled, loading, and `focus-visible` states. Buttons use an in-place pressed surface change, never a layout-shifting scale transform. Keyboard focus always has a visible ring. Async buttons preserve their dimensions and focusability, replace original icons with a spinner and text-only action label, use the same muted visual hierarchy as disabled controls while retaining focus, set `aria-busy` and `aria-disabled`, and reject duplicate submission. Row actions own independent busy state.
- Creation and edit workflows use the responsive `DialogContent` sheet variant: a non-blocking 600px right-side Sheet on larger screens and a bottom Drawer on phones. The desktop Sheet keeps the page visible and does not close on outside click; the phone Drawer uses a restrained scrim and permits tap-outside/Escape dismissal. Both have bounded height, independently scrolling bodies, action footers kept visible, and a 12px visible corner treatment. Destructive confirmations remain centered, fully blocking dialogs.
- Evidence Connector creation is a single provider-specific form grouped into
  Basic information, Connection information, and Read scope. Render only fields
  used by the selected kind. Mark required fields explicitly; every optional
  field must say `Optional` in its localized placeholder and remain empty until
  submission so the hint stays visible.
- Connector list inputs use compact removable values plus one text input. They
  accept Enter, commas, and pasted delimiters while preserving draft text during
  editing; confirmation trims whitespace, drops empty values, and deduplicates.
  Loki equality operators use a single-value input and set operators use this
  list input.
- PostgreSQL/MySQL require an explicit TLS security selection. Full certificate
  and hostname verification is the default; encryption-only is visibly labeled
  as not verifying server identity. The optional CA PEM appears only for full
  verification and identifies the database provider as its source. Switching
  modes clears stale CA state. Plaintext and automatic fallback are unavailable.
- `Create and verify` is disabled only while its request is running. Submission
  validates all visible fields, focuses the first invalid field, and keeps both
  values and field-level errors after remote failure. Remote failures display the
  server's sanitized actionable reason, such as the observed and required
  provider versions or the exact failed PostgreSQL connection/read-only check.
  Request-shape failures name the first invalid field instead
  of displaying a generic validation error. A successful response is
  already healthy and introspected; the client never creates a temporary row and
  chains follow-up verification calls.
- Connector failure banners render allowlisted diagnostic identifiers such as a
  PostgreSQL SQLSTATE alongside the code-authored explanation. They never render
  raw driver messages, provider response bodies, connection strings, or secrets.
- Third-party HTTP connectors accept canonical HTTP or HTTPS origins, including
  authenticated deployments on trusted private networks. The form does not
  silently upgrade or reject HTTP; redirects and embedded URL credentials remain
  disabled.

## Operational Surfaces

- Use 1px-bordered, low-radius panels for forms, inspectors, dialogs, and repeated data groups. Do not place decorative cards inside cards.
- Tables and record streams are the default list surface. Tables use 36px muted header rows and 40px data rows; wide tables use a horizontal scroll container, stable column widths, truncation for unbounded technical strings, and copy affordances where appropriate. Every operational table declares its columns with `TableColumns`; it keeps fixed leading or trailing pixel widths separate and normalizes the remaining content-column weights. Checkbox selection and single-icon action columns use 64px; two-icon action columns use 104px; text-command columns reserve their command width explicitly. Numeric and relative-time columns use tabular figures. List timestamps are compact relative time for seven days, then a localized stable date with a full timestamp in the hover title. Every control-plane table uses this shared operational-table treatment; route-specific visual table overrides are prohibited.
- When a record-list row selects the detail surface beside or below it, keep the selected row on a neutral muted surface with a 2px inset ink marker and expose its state through `aria-pressed`; do not use a semantic status color or only change the text color.
- A table status is one concise semantic marker: use the shared `table-status` dot plus label for lifecycle or verification state. Do not pair a redundant success/error icon with the same label.
- Use `status-badge` only for compact categorical metadata such as provider or source. It is a neutral, hairline-bordered 6px tag, never a substitute for a lifecycle-state marker.
- Every row that has a detail view exposes its primary field as a named, keyboard-accessible detail link. A visual arrow may support the affordance but cannot be its only indication.
- Empty, loading, and error states are first-class. First loads use structural table/list skeletons with the same row height as real data; refresh retains the existing data. An empty collection renders a dedicated, `aria-live="polite"` Empty State outside the table rather than a header-only table; nested collections use a compact in-surface empty state with a divider instead of a second card. Filtered-empty states preserve filters and offer Clear filters. Errors expose a Retry action when their source can be fetched again. Client UI renders localized safe messages and keeps stable backend codes out of ordinary display.
- State-changing controls use visible labels for their reason and target fields; placeholders may supplement a label but never provide the only visible field context. Put the fields above the related commands and collapse the grid to one column on narrow screens.
- In an inline form grid, align the associated command with its input or select control baseline (`self-end`), never with the field label; stacked mobile layouts keep the command after its related field.
- Workflow state is literal: a missing stage is pending only for an active run. A terminal historical run that predates a stage marks it skipped with an explanation; never show it as still queued.

### Investigation Single-Page Workbench

- Investigation detail has no page-level tabs. The continuous reading order is:
  compact status and incident error, diagnosis and recommended action, execution
  process, unused Connectors, then contextual node detail in a responsive Sheet
  (right side on desktop, bottom Drawer on phones).
  Workspace ID, duplicated evidence lists, model costs/tokens, and raw technical
  snapshots are not ordinary page content. The investigation ID is an icon copy
  action rather than permanent metadata.
- The diagnosis area is an unframed two-column layout, not nested cards. Root
  cause, causal chain, and confirmed facts are primary; code diagnosis, next step,
  and non-empty evidence gaps are secondary. Before report publication, show the
  current phase and collected operation/evidence counts. A failed investigation
  leads with understandable incident and failed-step context.
- The read-only directed lane graph defaults to `Compact`: event input, each
  Connector/source operation, live phase, and result. `Full` restores decision,
  synthesis, and verification nodes. Hidden-node transitive edges may reconnect
  visible ancestors and descendants, but never fabricate order between parallel
  operations. Locating a hidden active or evidence-owning node switches to Full
  before focusing it.
- Investigation stages and rounds form the horizontal axis; Lode control, each
  used Connector snapshot, and each used repository snapshot form stable vertical
  lanes. Stage headers and lane bands are structural React Flow nodes, so labels,
  bands, edges, and operation nodes always share the same pan/zoom transform.
  Every persisted invocation is independent. Repeated calls remain
  separate nodes in one lane. Nodes have stable dimensions and show name, type,
  purpose, icon plus textual status, duration, record count, and evidence count.
  Color only reinforces status. Running motion is disabled by
  `prefers-reduced-motion`.
- Hover and keyboard focus expose a compact summary. Activating a persisted node
  or a report `Evidence #ID` opens the same responsive Radix Sheet. Closing it
  restores graph viewport and focus. Canonical SSE and five-second refresh keep
  the user's selection and silently refresh open detail when `event_cursor`
  advances. Drag, connect, delete, and edit are unavailable.
- The drawer uses fixed product presenters by node and Connector type. SQL uses a
  code block plus dynamic-column table; Loki uses time, level, labels, and message;
  Elasticsearch/OpenSearch uses structured conditions, sorting/range/aggregation
  summaries, and record tables; HTTPS uses method, path, parameters, status, and
  typed fields; Command uses search target, file scope, output lines, and error
  summary; source reads use repository/revision/path/line/code presentation. A
  rejected proposal is clearly not executed. Unknown shapes use bounded key-value,
  list, count, or table presentation. Visible `JSON.stringify` and formatted raw
  JSON fallbacks are forbidden.
- Unused frozen Connectors live in one collapsed region after the graph and show
  only name, type, and a persisted rejection reason when present. Below 768px the
  canvas becomes a round-grouped vertical list with identical selection and
  Sheet behavior; the bottom Drawer is full width. The page itself never gains
  horizontal overflow. Result tables and long query/log/code content scroll only
  inside their own surfaces.

## Status And Feedback

- Use compact 6-8px status chips only for discrete state, with text labels in addition to color. Keep status chips compact rather than converting ordinary metadata into pills.
- Toasts acknowledge completed asynchronous actions. Errors remain visible at the failed surface and must not be toast-only.
- Empty data states are separate from table headers and centered in their full panel on both axes. Shared `EmptyState` content owns the message and action; its parent owns the panel border and surface.
- Destructive actions require confirmation where the underlying workflow supports it. The initial focus in a confirmation dialog is always its safe cancel action, never the destructive action or close affordance. Disabled actions explain their unavailable state in nearby text or an accessible label.

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
  removal is confirmed and adding uses the responsive Sheet with a searchable
  active-user combobox.

## Authenticated UI Checklist

- Reuse `AppShell`, sidebar, topbar, and shared UI primitives.
- Keep shared Vercel Dashboard shell/surface rules in `app/dashboard.css`, loaded
  after `globals.css`; keep investigation-domain visualizations in
  `app/investigation.css` rather than coupling them to the shell layer. The
  locale layout loads styles in this order: global Tailwind/tokens, React Flow
  base CSS, investigation token overrides, then the shared Dashboard shell.
- Implement only the frozen current API, localization, permission, and business
  contracts; visual alignment must not replace real behavior.
- Verify desktop, tablet, and mobile layouts. Verify mobile navigation, account
  menu, row detail navigation, form submission, localized table/tab scrolling,
  and canonical SSE reload after each shared-shell change.
