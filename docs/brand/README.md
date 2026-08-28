# Lode Brand Guide

## Positioning

Lode is an evidence-driven incident investigation platform for engineering and
operations teams. It turns production signals, source context, native system
reads, and model-assisted analysis into bounded, auditable root-cause reports.

- Chinese descriptor: `证据驱动的智能故障调查平台`
- English descriptor: `Evidence-driven incident investigation`
- Chinese tagline: `从信号到有据可证的根因。`
- English tagline: `From signal to substantiated cause.`
- Brand traits: precise, composed, trustworthy, technical, evidence-first

## Logo Idea

The **Bounded Scope** mark expresses Lode's controlled analysis model. The
paired braces establish an explicit, closed scope. The blue square is a
structured evidence input evaluated inside that boundary. Together they reflect
Lode's fail-closed authorization, bounded context, and auditable conclusions.

The mark avoids generic AI sparkles, shields, magnifying glasses, chart bars,
and the temporary triangle previously used by the interface.

## Assets

| Asset | Use |
| --- | --- |
| `apps/web/public/brand/lode-mark.svg` | Preferred mark on light surfaces |
| `apps/web/public/brand/lode-mark-dark.svg` | Preferred mark on dark surfaces |
| `apps/web/public/brand/lode-mark-mono.svg` | One-color printing, embossing, and masks |
| `apps/web/public/brand/lode-mark-512.png` | Transparent 512px mark for light surfaces |
| `apps/web/public/brand/lode-mark-dark-512.png` | Transparent 512px mark for dark surfaces |
| `apps/web/public/brand/lode-lockup.svg` | Horizontal lockup on light surfaces |
| `apps/web/public/brand/lode-lockup-dark.svg` | Horizontal lockup on dark surfaces |
| `apps/web/app/icon.svg` | Theme-aware browser and app icon |

The React source of truth for product UI is
`apps/web/components/brand/lode-logo.tsx`. It follows the active theme and uses
the product's existing `--link` token for the highlighted difference.

## Color

| Role | Light | Dark | Notes |
| --- | --- | --- | --- |
| Lode Ink | `#171717` | `#EDEDED` | Wordmark, scope boundary, primary text |
| Evidence Blue | `#0070F3` | `#3291FF` | Structured evidence core, links, focus |
| Canvas | `#FFFFFF` | `#0A0A0A` | Product surfaces |
| Verified | `#00C389` | `#00C389` | Confirmed evidence only |
| Warning | `#F5A623` | `#F5A623` | Incomplete or caution states |
| Critical | `#EE0000` | `#FF4D4F` | Failures and critical incidents |

Use Evidence Blue as a signal, not a fill color for large surfaces. Operational
status colors retain their semantic meanings and must not recolor the logo.

## Typography

Use Geist Sans for the wordmark and interface copy, and Geist Mono for evidence
IDs, hashes, timestamps, queries, and technical metadata. Keep letter spacing at
zero. Use sentence case in English and concise, direct phrasing in both locales.

## Usage

- Keep clear space around the mark equal to one quarter of the mark width.
- Minimum digital size: `16px` for the mark and `96px` for the lockup.
- Prefer the color mark where Evidence Blue is available; otherwise use mono.
- On dark backgrounds, use the dark asset or the theme-aware React component.
- Do not rotate the mark, add gradients or shadows, change the brace relationship,
  put the mark in a rounded badge, or replace Evidence Blue with a status color.
- Product screens may pair the mark with the `Lode` name in live Geist type;
  exported materials should use the supplied lockup.

## Voice

Lead with what is known and preserve uncertainty. Prefer "Evidence indicates..."
over "AI found...". Avoid exaggerated automation claims, playful incident copy,
and language that implies confidence without supporting evidence.
