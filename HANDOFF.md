# HANDOFF — BOHUN / THE FIELD (bohundefence.com map), 2026-10-11

Written by Claude (Sonnet 5.5) at the Founder's request: a complete handoff with no loss. Nothing was deleted, renamed or rewritten.
Language of the product UI: `atlas.html` is English; `field.html`, `flows.html`, `game.html` are Russian (review UIs).

## 0. Where things are

| What | Where |
|---|---|
| Repository | https://github.com/nab0k/bohun-the-field |
| This branch | `claude-complete-handoff` (based on local `main` 04c179e + snapshot of uncommitted work + external materials) |
| Extra preserved branches | `handoff/local-feature-signals-v1` (916ff87), `handoff/local-publish` (4772869): local-only "Signals v1" work, see section 6 |
| `origin/main` | NOT touched. It has 96 newer, independent commits (collectors, source registry, ADR 012, tests, docs). It does not contain the Atlas work after b55c7d4. |
| Local backup (off GitHub) | `~/Projects/bohundefence-field-backup-2026-10-11/`: `bohundefence-field-working-tree.zip` (34 MB, no secrets), `bohundefence-field-all-refs.bundle` (418 MB, every local+remote ref) |
| Not in git (too large / third party) | `handoff/external-site-materials/ASSETS-NOT-IN-GIT.txt` lists them (Kenney/Slavic packs, NE1_50M_SR_W.zip, Unity/Godot/Blender packs, PDFs); they live in `~/Downloads/Bohundefence/Сайт/Assets/` |
| Secrets (never committed) | `.env.local` (Inoreader app id/key/tokens, AISStream key), `data/inoreader/private/` (feed backups). Recreate from the owner's password manager. |

Merge note (dry run, `git merge-tree`): merging this branch with `origin/main` has ONE conflict: `data/sources/registry.csv` (add/add; `src/atlas/main.js` auto-merges). Resolve by keeping `origin/main`'s registry and re-applying the local rows by hand. Do it in a PR, not by force.

## 1. Run it

```sh
npm install
npm run dev          # http://localhost:5174  (vite --port 5174)
npm test             # core + assets + flows + field + atlas checks, no browser needed (all pass on 2026-10-11)
npm run build        # multi-page build: index, game, flows, field, atlas (builds; atlas chunk ~2 MB, warning only)
npm run preview      # http://localhost:5175
```

Node 26 is assumed (JSON imports with `with { type: 'json' }`). Python scripts need `psycopg[binary]` (TED import). A `.env.local` is only needed for the AIS ships layer and Inoreader scripts; the pages open without it.

Checked on 2026-10-11 against the dev server: `/`, `/game.html`, `/flows.html`, `/field.html`, `/atlas.html` all return 200 and load; `atlas.html` map style loaded (MapLibre `loaded()` true); `npm test` passes; `vite build` succeeds.
Screenshots: viewed in the Claude built-in browser pane only; image files were NOT saved into the repo (the pane tool cannot export files).

## 2. Pages (what is real, what is a stub)

| Page | Role | Status |
|---|---|---|
| **`/atlas.html`** | **Current main product line** after the 2026-10-09 strategy pivot: a world map of the defence industry. MapLibre GL 6 (globe by default, `?projection=mercator`) + deck.gl 9.4 overlay. English UI. | Working: entry I NEED / I HAVE, SELL/BUY journeys (demo data), relief/hillshade looks, borders from Natural Earth (Crimea in Ukraine), industry layer (72 Wikidata companies in DE/FR/IT/PL/CZ + 17 UA names without places), Austria 50 companies, undersea infrastructure (47 items), LIVE layer (military/civil aircraft from adsb.lol, ships via AISStream relay, Finnish trains), night side with city lights following the real terminator, world capitals + 24 oblast centres + Simferopol, search, left panel/cards, country view, 30k-point scale demo. Simulated: all journey actors/verdicts/experts, flows, "DEMO" cargo. |
| **`/field.html`** | v0.4 "game-like" field: dark olive RTS look on real Natural Earth geography, Phaser. Earlier main prototype. | Working as a demo: two guided scenarios (I NEED "find a supplier", I HAVE "enter a market"), flows mechanics, minimap, whole-planet wrap. Art = code-drawn placeholders; positions are TEST positions. Russian UI. |
| `/flows.html` | Flows mechanic on a tilted (45°) whole-planet grey map: redirect flows (limit 3), ambient traffic, Bohun rides, minimap. | Working as a mechanic demo; grey placeholder art. |
| `/game.html` | Old 3-step demo (send Bohun Lviv→Kyiv, cargo, research) on the Codex technical SVG map. | Works; to be archived. Classic rider PNG is one static pose. |
| `/` (`index.html`) | Original landing draft (scroll presentation + game mode, `src/scene.js`). | Works; superseded; not to be expanded. |

**field vs atlas:** `field.html` is a custom Phaser world drawn from Natural Earth vectors (own camera, own tilt projection, painted-style placeholders, a "game"). `atlas.html` uses a ready-made real map engine (MapLibre globe + OpenFreeMap vector tiles) with deck.gl objects; it is the base for the "map of the whole defence industry" direction. Both reuse the same rules core `src/flows/core.js`.

## 3. Code map

- **Rules (no DOM/Phaser):** `src/flows/core.js` (flows, Dijkstra routes fast/cheap/no-air, ambient traffic, Bohun rides, coins at banks; events `flowStarted/Stopped`, `bohunArrived`, `dossierRequested`, ...), `src/flows/data/network.js` (≈48 test nodes, ≈70 corridors, TEST positions), `src/flows/data/world-frame.json`. `src/game/core.js` is the older 3-step rules module.
- **Atlas:** `src/atlas/main.js` (UI, layers, cards, search, camera), `journeys.js` (SELL/BUY on top of flows core), `live.js` (aircraft/ships/trains), `night.js` (terminator), `scale-demo.js` (30k points), `atlas.css`. Data in `public/atlas/*.json|geojson`, models in `public/atlas/models` (Kenney CC0, provenance in `PROVENANCE.json`), MapLibre worker copy in `public/atlas/vendor/` (see Known issues).
- **Field:** `src/field/main.js` (Phaser view), `scenarios.js` (two scenarios, framework-free), `terrain.js`, `public/field/hero-geo.json`.
- **Flows view:** `src/flows/main.js`. **Game view:** `src/game/main.js`, `scenario.js`, `geo.js`. **Landing:** `src/main.js`, `scene.js`, `world.js`, `art.js`.
- **Build scripts (`scripts/`):** `build-atlas-borders.mjs`, `build-world-base.mjs`, `build-field-hero.mjs`, `build-industry.mjs`, `build-companies.mjs`, `build-places.mjs`, `build-undersea.mjs`, `reproject-relief.mjs` (Natural Earth relief → tilted guide), `tile-image.mjs`, `inspect-png.mjs`, `copy-maplibre-worker.mjs`, `fix-kenney-glb.mjs`; Inoreader: `inoreader-auth.mjs`, `inoreader-organize.py`, `build-opml.mjs`; `run_ted_daily.sh`. Generated JSON names its generator and source.
- **Tests:** `scripts/test-core.mjs`, `test-assets.mjs`, `test-flows.mjs`, `test-field.mjs`, `test-atlas.mjs`.
- **Dev proxy:** `vite.config.js` proxies `/live/adsb`, `/live/ais`, `/live/rail` and mounts `dev/aisstream-relay.mjs` (needs `AISSTREAM_API_KEY`). Production would serve `/live/*` from a Cloudflare Worker (not built).
- **Data pipelines:** `collectors/ted/` (TED tenders → Supabase), `.github/workflows/ted-daily.yml` (daily 07:15 UTC, uses repo secret `SUPABASE_DB_PASSWORD`), `data/inoreader/bohun-feeds.opml`, `data/sources/registry.csv`, `bohun_A_*.py/csv` (government-source discovery/import, root-level scratch scripts), `data/notion/*` (Austria companies/geocode).

Libraries: Vite 8, Phaser 3.90 (pinned), GSAP 3.15, maplibre-gl 6.13, deck.gl 9.4 (core, layers, geo-layers, mesh-layers, maplibre), loaders.gl 4.5 (gltf). Vanilla JS modules, no framework.

## 4. Graphics and art (Classic = ChatGPT image generation)

- `handoff/classic-tilt-v03/`: tilted 45° guides and tasks for Classic: `TASK_CLASSIC_BACKGROUND.md`, `PROMPT_CLASSIC_PROBE1.md`, Natural Earth relief guides `first-scene-tilt45-relief-{clean,borders,guide}.png` (use `-borders` as the file for Classic), older flat guides (superseded), `world-layout-tilt-draft.json` (v0.3 projection anchors).
- `handoff/classic-probe1-result/`: **Classic's first painted probe (Ukraine)** `bohun-bg-tilt45-v1.png` (1586×992) + `-guide40.png` overlay. Geometry is approximate: water-mask IoU 0.63, city anchors 70–100 px off (tolerance ±16 px at 2560). Not integrated.
- `handoff/classic-bohun-v2/TASK_CLASSIC_BOHUN_V2.md`: brief for a new Bohun sprite (iso 45°, 5 directions, walk cycle).
- `handoff/classic-world-tiles-v1/`: INCOMPLETE (only `world-overview-guide.jpg`; earlier tile attempts were invalid and removed). Redo with `tile-image.mjs` after converting TIFF-typed PNGs (macOS `sips` writes TIFF even into `.png` names).
- `public/assets/bogun/bohun-rider-classic-v1.png` (+ PROVENANCE.md): Classic rider, ONE static pose (not an animation); used by game/flows/field/atlas as billboard.
- `handoff/external-site-materials/`: Classic's Canvas slices v0.2 and v0.3 (README, ART_BIBLE, TECH_ARCHITECTURE, source), `bohun_playable_v01`, GIFs, the painted reference map `a_wide_richly_detailed_painterly_isometric_carto_2.png`, a rider reference image.
- Kenney/third-party packs are NOT in git (see `ASSETS-NOT-IN-GIT.txt`); only the GLB models actually used by atlas are in `public/atlas/models`.

## 5. Decisions already made (do not lose)

- **Whole planet**, 45° tilt everywhere including the minimap, start with Bohun in Ukraine; leave room for a later exit to space.
- "Draw everything as it is, but nothing shoots or destroys" (08.10). Ports, stations, airfields are nodes.
- Objects requested: mine; 3 factory sizes; drone swarm, copter/Shahed/interceptor drones; helicopters, fighters, cargo planes; EW; soldiers; trucks/platforms, MG pickups; sea drones, destroyers, submarines; trains/wagons; gold coins/green banknotes; banks. Things move from time to time; Bohun rides point to point; minimap in a corner; minimal mechanics: zoom, click for info, redirect flows ("Потоки").
- Process wanted by the Founder: draw map + objects first (Classic), then mechanics, then glue. Painted background = one image + separate sprites + code-driven movement; 5 directions + mirror.
- **Strategy pivot 2026-10-09 ("only this course"):** bohundefence becomes a map of the whole defence industry (taxonomy/supply chain, companies, regulation by country, associations, conferences, investors, news feeds via Inoreader, live layers: front, aircraft, ships, trains) plus a separate Ukraine supply/front game. Open decisions live in Notion "Стратегия Сергія (09.10.2026)": .com vs .org, people data, game vs the 08.10 no-combat rule, budget, front-data source.
- Public guardrails: category-level signals only; no RFQs, quantities, specs or customers; "DEMO" labels on fictional data; real companies never take part in journeys; notice "Open data. Not clients or partners of Bohun"; Russia/Belarus excluded from industry data; borders = Ukrainian representation (Natural Earth countries_ukr), Crimea inside Ukraine; run a narrow legal check on "Sourcing & Procurement Support" and "Tender and procurement landscape"; do not copy Cossacks assets.
- ADR 012 (Supabase canonical) is on `origin/main` (docs only).
- Kling AI: any paid generation needs prompt/model/cost shown and explicit sign-off.

## 6. Other local work preserved on separate branches

`handoff/local-feature-signals-v1` / `handoff/local-publish` carry "Signals v1: AI pipeline every 15 minutes and SIGNALS layer on the map": `src/atlas/signals.js`, `src/atlas/iso.js`, `pipeline/test.mjs`, `vite.site.config.js`, `public/dev/iso-options.html` (three isometric object style options), and deletions of scale-demo/places/companies builders. They diverge from this branch's atlas (they were made in a separate local line); review before merging, do not merge blindly.

## 7. Known issues

- Phaser console error `Framebuffer status: Incomplete Attachment` in the Claude built-in browser pane (field.html); scene still renders; not reproduced in a normal browser (not tested there).
- The built-in pane throttles `requestAnimationFrame` to ~1–2 fps: real-time smoothness is unmeasured. Step frames with `__phaser.loop.step()` (field) or `__step` (atlas) for tests. Headless Chrome was rejected by the owner.
- MapLibre worker must be loaded from the verbatim copy in `public/atlas/vendor/` (Vite dev injects its client into the original and it never answers). `npm test` checks the copy matches the installed version; rerun `node scripts/copy-maplibre-worker.mjs` after upgrading maplibre-gl.
- OpenFreeMap public instance has no uptime guarantee; LIVE layer APIs have no SLA and may rate-limit.
- All positions in flows/field/atlas journeys are TEST placeholders; time and price are conditional units.
- `data/sources/registry.csv` conflicts with `origin/main` (see section 0). Local `main` and `origin/main` have diverged (8 local vs 96 remote commits).
- Working-tree junk preserved on purpose, safe to remove later with approval: `*.bak`, `*.bohun-backup`, `scripts/inoreader-auth.mjs.backup`, `inoreader-folder-plan.backup.json`, `logs/ted_daily.log`. Two git worktrees in `/private/tmp` are marked prunable.
- Phone layouts verified only at 375 px for atlas; not verified elsewhere.

## 8. Not done / ideas worth keeping

- Painted art is not integrated anywhere: background from Classic (re-run probe 1 with the relief guide + percentage city positions Warsaw ≈28%/38%, Lviv ≈36%/50%, Kyiv ≈54%/45%), world in 12 tiles, ~100 object frames (Bohun step cycle 6–8 frames × directions, wagon, train, ship, plane, ...). Fallback "path B": code keeps geometry through masks, Classic paints land/sea textures.
- Style decision pending: calm/dark vs bright/dense like the reference painting.
- Space exit, Ukraine detail tier, mine/factory/bank real placement, flows on real routes, real front data, people data, contact form wiring.
- Cloudflare Worker for `/live/*`; production stack (Next/React + Phaser) is a hypothesis, untested.
- Notion pages (source of the plan, journal, decisions): working page `3f3c42d0b11b81e99cd3e02abc6bed4a`, strategy `3f4c42d0b11b81bdb666c0912ba22c0e`; old HQ `3f2c42d0b11b811b92dcc821e8d1de84` (archive).

## 9. Suggested next step

1. Decide the single code line: `atlas.html` (industry map) vs `field.html` (game). Per the 09.10 pivot, continue `atlas.html`; keep `field.html` as the game/mechanic reference.
2. Open a PR `claude-complete-handoff` → `main`, resolve `data/sources/registry.csv`, review `handoff/local-feature-signals-v1`.
3. Re-run Classic probe 1 with `first-scene-tilt45-relief-borders.png`; accept only after overlay check (coast, Dnipro, Crimea, anchors ±16 px).

## 11. Скриншоты (handoff/screenshots-2026-10-11/)

- `atlas-austria-companies.jpg` — atlas сейчас: левая панель, кластеры Австрии, список.
- `atlas-scale-demo-clusters.jpg` — демо масштаба, кластеры с кольцами категорий.
- `atlas-live-baltic.jpg` — слой LIVE (самолёты, суда, поезда) над Балтикой (старый вид, до левой панели).
- `atlas-ukraine-oblast-centres.jpg` — подписи областных центров Украины.
- `field-v04-ui-only.jpg` — field.html: интерфейс и потоки работают, но поле Phaser во встроенном браузере Claude
  не рисуется (WebGL «Framebuffer status: Incomplete Attachment»); в обычном браузере проверять заново.
