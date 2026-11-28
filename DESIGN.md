# Design System — 星图画廊（DouK-Lite · 石墨工作台）

Preserves the user-confirmed **「石墨工作台」** dark moss-graphite + warm copper aesthetic identity with refined spacing, unified control heights, and compact first-screen layout.

## Color System (Unchanged Original Palette)

| Token | Value | Role |
| --- | --- | --- |
| `--ground` | `#0f1211` | Primary app background & dot-grid sidebar |
| `--panel` | `#161b18` | Panels, stat strips, cards, dialogs, paste console |
| `--field` | `#121614` | Recessed inputs, media wells, filmstrip & video wells |
| `--raise` | `#1d2420` | Hover surface, active nav/mode background, toast |
| `--line` | `#262e29` | Primary 1px borders and dividers |
| `--line-2` | `#333c36` | Secondary interactive borders |
| `--ink` | `#e7ebe6` | Primary text |
| `--mid` | `#a6b0a9` | Secondary labels & ghost button text |
| `--muted` | `#7c8880` | Meta, timestamps, inactive nav labels |
| `--copper` | `#e0a64b` | Primary CTA fill, active nav left indicator, active pill tabs, scrubber thumb, ComfyUI pick highlight |
| `--copper-hi` | `#f0bc66` | Copper hover state & progress highlight |
| `--ok` / `--skip` / `--bad` / `--live` | `#7fbf9a` / `#c7a87a` / `#e06b5a` / `#7fafc9` | Status dots and LivePhoto type tag |

## Typography

- **UI Sans (`--sans`)**: `"Bahnschrift", "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif`
- **Data & Code (`--mono`)**: `"Cascadia Mono", "Cascadia Code", Consolas, "SFMono-Regular", monospace` with `tabular-nums` for metrics, timestamps, counts, and timecodes.
- **Scale**: `24px` (`h1` view heads), `22px` (`stat-num`), `18px` (`lib-stat-num`), `16px` (`h2` section heads), `14px` (body), `12.5px`–`13px` (controls & post titles), `11px`–`12px` (meta & badges).

## Layout Refinements

- **First-Screen Priority**: Replaced multi-line marketing hero headings with compact `24px` `.view-head` headers so the 4-column `#galStats` strip, `#creatorsBar`, and `#feed` open directly above the fold.
- **Unified Canvas & Toolbar Rhythm**: Consistent `max-width: 1260px` canvas (`1420px` in `frame-mode`) and uniform `34px` control height across `.primary`, `.ghost`, `.search`, `.select`, `.discover-toggle`, `.discover-block-input`, and `.segmented`.
- **Unified Stat Strips & Dialogs**: Both `#galStats` and `#creatorStats` share a 4-column divided card layout (2×2 at `<=860px`); `#comfyDialog` and `<textarea>` inputs use clean tokenized CSS without inline styles.
- **Mobile Drawer (`<=860px`)**: Compact sticky header bar with `#mobileMenuBtn` toggle (`#railDrawer`) so navigation and save-path controls fold cleanly without crowding the viewport.
