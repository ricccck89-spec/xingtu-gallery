# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Solo personal creator, visual collector, and AI-workflow operator running locally on a Windows or macOS desktop during extended visual curation sessions.

## Product Purpose

**星图画廊（Xingtu Gallery）**（core engine `DouK-Lite`）is a pure-local desktop media archive and creator workbench. It organizes downloaded Douyin and Xiaohongshu creators and posts on disk, lets the user browse recommendation feeds or parse single/creator links, packs LivePhoto pairs (`HEIC + MOV`), scrubs and exports lossless video frames (`PNG`), and batch-picks gallery images for local ComfyUI processing. Success means getting straight to creators and media on the first screen with zero marketing clutter, fast visual scanning, and reliable local tool execution.

## Positioning

Zero-cloud, folder-native local media archive with no external database—browsing creators, filtering works, scrubbing single video frames, and feeding ComfyUI operate directly on real files in the local save directory (`Volume/`).

## Operating Context

- Launched locally via Chrome application window (`http://127.0.0.1:8765`, auto-exits via SSE heartbeat when closed).
- Extended desktop sessions switching across four main workflows:
  1. **画廊（Gallery）**：Creator wall + unified post feed with type/creator/search/sort filters and ComfyUI multi-select pick mode.
  2. **博主详情（Creator Detail）**：Single creator archive, quick newest-post download, remark editing, and folder opening.
  3. **发现（Discover）**：In-app Xiaohongshu & Douyin recommendation feeds, keyword/category filtering, creator preview, plus collapsible link-parse & download console (`#viewDownload`).
  4. **工具（Tools）**：LivePhoto (`HEIC + MOV`) batch packer and frame-by-frame video scrubber (`Space`, `←/→`, `Shift+←/→` / `J/L`, `Home/End`, `S`, `C`, plus ffmpeg fallback).
- Connected local ComfyUI endpoint (`http://127.0.0.1:8188`) via `#comfyPickBar` and `#comfyDialog`.

## Capabilities and Constraints

- Pure vanilla `DouK-Lite/web/index.html`, `DouK-Lite/web/style.css`, and `DouK-Lite/web/app.js` with zero build step or bundler.
- Must preserve 100% of existing DOM IDs, JavaScript event hooks, keyboard shortcuts, drag-and-drop asset export, backend HTTP endpoints, and dialogs (`#settings`, `#discoverPreview`, `#viewer`, `#comfyDialog`, `#comfyPickBar`, `#toast`).
- Full visual & layout scope covers all 4 views (`#viewGallery`, `#viewCreator`, `#viewDiscover`, `#viewTools`) and all dialogs/overlays.
- Real-time data integrity: all counts, sizes, download states, and error states reflect actual local files and live API responses.

## Brand Commitments

- Brand name: **星图画廊**（subtitle: **博主作品库**）.
- Existing brand icon assets in `DouK-Lite/web/` (`icon.png`, `icon-180.png`, `icon.ico`).
- **Binding Aesthetic Identity（「石墨工作台」 / Graphite Workbench）**：User explicitly pinned the original dark moss-graphite (`#0f1211` / `#161b18` / `#121614` / `#1d2420`) and warm copper (`#e0a64b` / `#f0bc66`) color palette, dot-grid sidebar, twin-star mark, copper primary actions, pill filter tabs, and mono-data numeric typography. Layout improvements must not alter this palette.

## Evidence on Hand

- Existing implementation in `DouK-Lite/web/index.html`, `DouK-Lite/web/style.css`, and `DouK-Lite/web/app.js` plus backend `DouK-Lite/douk_lite/http_server.py`.
- Local media archive in `Volume/` (creator folders, image sets, videos, LivePhotos, extracted frames).
- No external customer testimonials, SaaS pricing, or cloud metrics exist and none may be fabricated.

## Product Principles

1. **Media leads; chrome recedes** so photography and video retain full visual authority.
2. **Direct to work on the first screen**—no landing-page slogans or wasted hero height.
3. **Direct manipulation and keyboard fluency** over ceremony (batch pick, frame scrub, drag-out, one-click folder access).
4. **Honest local state**—real file counts, disk sizes, download progress, and clear empty, loading, and error states.
5. **Zero-build durability**—clean semantic HTML and structured CSS design tokens without external runtime dependencies.
