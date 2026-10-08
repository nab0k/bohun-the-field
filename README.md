# BOHUN / THE FIELD — vertical-slice prototype

Local spike. Not deployed, not connected to any repo or production site.

Stack: Vite + Phaser 3.90 (pinned) + GSAP, vanilla JS. All art is drawn in code (original, deliberately crude).

    npm install
    npm run dev      # http://localhost:5174
    npm run build

What it tests
- Presentation Mode: scrolling moves the camera through the world; HTML overlays hold all real text.
- I NEED / I HAVE: the visitor state changes the world (highlights, routes).
- PROPULSION signal -> dossier panel (copy from Homepage Copy v1).
- Game Mode: click the small figure patrolling next to BOHUN -> "Field control". Select PROPULSION -> RESEARCH
  -> candidates appear -> QUALIFY one -> ESTABLISH CONTACT -> route drawn. ESC exits.
- Data: one model in `src/world.js` drives both site and game.

URL helpers: `?mode=game`, `?emph=need|have`, `?dpr=1`, `?renderer=canvas`.

Open items (not decided here)
- Copy for sections 04/08 and "Tender & procurement landscape" need a legal/claims check.
- Contact form is not wired.
- Production stack (Next/React + Phaser) is a hypothesis, not tested.
- Cross-linking .com <-> .org must respect the NGO Statute section 5.

---

# Game module (HQ section 0.8, Claude's part)

Follows "Bohun world — общий контракт v0.1" (Codex, 7 Oct 2026; pasted by the Founder, not yet stored in the repo).
Separate from the landing prototype above, which is a draft and is not touched. Owner of these files: Claude:
`src/game/*`, `game.html`, `scripts/test-core.mjs`. Codex integrates; nobody else edits them meanwhile.

Run: `npm run dev`, open `/game.html`. Logic tests: `npm test` (15 checks, no browser needed).

Contract v0.1 mapping
- Commands: `moveBohun(destinationId)`, `dispatchCargo(routeId)`, `research(nodeId)`, `resetDemo()`, `setMode('presentation'|'game')`.
  Each validates availability and returns `{ok}` or `{ok:false, reason}`; repeated clicks never create a second shipment or research.
- Events (`onEvent(type, payload)`): `bohunDeparted`, `bohunArrived`, `cargoDispatched`, `cargoArrived`, `researchStarted`,
  `researchCompleted`, `routeUnlocked`, `dossierRequested {dossierId, reason}`, `stateChanged`, plus `modeSet`, `reset`, `rejected {action, reason}`.
  The game never touches the page: `main.js` (the HTML layer) listens to `dossierRequested` and shows the card.
- Data: cities and the closed node carry `id`, `label`, `lon`, `lat`, `dossierId`; routes carry `id`, `fromId`, `toId`, `path`, availability
  (`lockedBy` until researched). Start ids: `lviv`, `kyiv`, `warsaw`, `lviv-kyiv`, `lviv-warsaw`. Route paths are demonstration lines.
- Geography is the source of truth. `src/game/geo.js` holds the single `geoToWorld(lon, lat)`; no other file computes x/y. It is a
  PLACEHOLDER (flat, no isometric tilt) until Codex supplies the contract projection.
- Camera: one owner at a time. `presentation` = the site drives it (the page may set `window.__view.cx/cy/zoomv`; pan and wheel are ignored),
  `game` = the player does. `resetDemo` keeps the mode.
- Cargo and resources are fictional (`DEMO CARGO`, a demo counter).

Files
- `src/game/core.js`: rules and state, no Phaser or DOM. `createGame(scenario, { onEvent })`.
- `src/game/scenario.js`: TEMPORARY data in contract shape. `src/game/geo.js`: placeholder projection.
- `src/game/main.js`: temporary view and panel; no geography or borders on purpose; Bohun is a gold triangle.

The three actions
1. Send Bohun: pick a city, he rides the route, arrival requests that city's dossier. States: waiting, moving, arrived.
2. Dispatch cargo: pick an open route, a neutral cargo moves, on arrival the demo counter grows. One shipment per route at a time.
3. Research: start on the closed node, progress shows, completion requests a dossier and unlocks the Lviv-Warsaw route. Repeating does nothing.

Not done / limits
- Dossiers hold demo text; approved copy is connected during integration. No sprites, no final art, no map.
- The existing landing scenario (research -> qualify -> contact in `src/scene.js`) is untouched and does not replace these actions.
- Contract items still open: projection and tilt, scene size, sprite sizes and anchors, frame data. `setMode` only gates input here;
  the site-side camera wiring is Codex's integration.
- `game.html` works in the dev server; no multi-page build entry yet.
- Installed versions: check `npm ls phaser vite gsap` (package.json uses caret ranges; the core needs none of them).


## Demonstration UI (Russian, plain language)

`/game.html` now shows the goal, the current step and the result of each of the three steps in plain Russian:
1. send Bohun Lviv -> Kyiv and open the city card; 2. deliver the demo cargo and raise the counter; 3. research and open Lviv -> Warsaw.
A pulsing ring on the map marks the target of the current step. Buttons "Вся карта" (whole 2560x1600 scene / back to the cities) and
"Сначала" (reset) stay at the top of the panel, which scrolls on narrow screens. Technical names (commands, events, camera owner)
are only inside the collapsed "Для разработчика" block. Background is still the technical SVG and Bohun is still a placeholder
triangle: no Classic artwork is wired in. Core change: `commands()` additionally returns a reason `code` (additive).

## Bohun sprite (Classic rider, first integration)

`public/assets/bogun/bohun-rider-classic-v1.png` is a byte-identical copy of the Classic original (sha256 and alpha facts in `PROVENANCE.md` next to it;
metadata, anchor and size target in `src/game/data/rider-sprite.json`). One static pose, anchored on the hooves, visible height 88 screen px on desktop
(72 on narrow screens) at the initial zoom, mirrored at draw time for westward travel. It slides along the route; there is no gallop animation.
`npm test` also checks the PNG hash, header and anchor. The image is not redrawn, recoloured or cropped. A 384 px working copy is made in memory only,
for clean down-scaling.

## Asset intake (for Classic's files)

1. Inspect a candidate: `node scripts/inspect-png.mjs <file.png>` (size, alpha, visible box, ground contacts, suggested anchor; warns about cropped
   figures and non-transparent corners). The raised foot of a pose must be dropped from the anchor by hand.
2. Copy the file unchanged into `public/assets/...` and fill its entry in `src/game/data/assets.json` (`rider`, `cargo`, `background`; `null` = not
   delivered, the built-in placeholder is used). Record the source in a PROVENANCE note.
3. `npm test` checks every non-null entry against the file (sha256, PNG header, anchor, size). A background entry must be 2560 x 1600 = the world.
The cargo code path (shipment image, mirrored for westward travel, removed on arrival and on reset) was exercised with a stand-in file and then removed;
no cargo artwork is integrated yet.

Local git: `main` in this folder, no remote.


## Flows prototype (grey graphics) — `/flows.html`

Whole planet, tilted 45 degrees (equirectangular + vertical squash cos 45), a tilted minimap, ambient traffic along corridors,
Bohun riding point to point, and the "redirect a flow" mechanic. Grey technical art only; every position is a TEST placeholder.
- Rules (no Phaser): `src/flows/core.js`; test network: `src/flows/data/network.js`; tests: `scripts/test-flows.mjs` (13 checks).
- World base: `node scripts/build-world-base.mjs <ne-countries-ukr.geojson>` writes `public/flows/world-tilt-base.svg` (Natural Earth, public
  domain, Ukrainian representation of borders; not a map of territorial control).
- Verbs: look (zoom, pan, minimap click, "Вся карта"), choose (click: Bohun rides there, card opens), redirect (source mine/factory ->
  pick a target -> choose a route option: fast / cheap / no air; max 3 flows). Time and price are conditional units; nothing shoots or destroys.
- Not done: painted art, the 5-direction sprite sets, space view, the Ukraine detail tier, final placements.

---

# /field.html — v0.4: the v0.3 slice's look on the "Flows" mechanics (8 Oct 2026)

Task: Notion "BOHUN / THE FIELD — рабочий план и роли (с 08.10.2026)" and its sub-page "Дизайн-директива Сергія (08.10) и механика «Потоки»",
built on the external vertical slice `~/Downloads/Bohundefence/Сайт/bohun-the-field-vertical-slice-v03/` (Canvas 2D, not copied in).

Taken from v0.3: dark olive RTS palette, diamond-cell ground, trees and villages, top bar with I NEED / I HAVE as map lenses,
hero card in presentation mode, objective box, selected-object panel with a command bar, minimap, mode HUD.
Not taken: v0.3's hand-drawn Ukraine outline (the HQ rule forbids drawing borders by eye) and its English scenario.

What is new
- Ground is painted once on a canvas from real data: `public/field/hero-geo.json` (Natural Earth countries_ukr + rivers + cities),
  generated by `node scripts/build-field-hero.mjs <dir with ne-*.geojson>` (source dir: the Codex package `bohun-map/data`).
  Same frame as the flows world (equirectangular + cos 45 squash), so every flows node lands where it should.
  Coasts and borders are vectors drawn over the cells; trees and villages are deterministic decoration, not data.
- Mechanics are `src/flows/core.js` and `src/flows/data/network.js` unchanged: same guided task (mine -> New York bank, fast vs cheap),
  up to 3 flows, ambient traffic, Bohun rides point to point, coins at banks. Object positions are still TEST positions.
- Objects drawn in code as small iso buildings (mine with headframe, three factory sizes with smoke, bank, port with crane,
  station, airfield); far zoom shows dots.
- Camera owner: presentation (site) by default, wheel and drag only after "ДВИГАТЬ КАРТУ"; minimap click takes the camera.
- Approved Homepage Copy v1 is used verbatim in the hero card; game UI is Russian for review.

Run: `npm run dev`, open `/field.html`. Checks: `npm test` (+4 field checks). Test hooks: `__choose(id)`, `__flows`, `__scene`.
Not done: Classic artwork (all art is code), animation frames, objects 2–3 (air, sea and land vehicles), real placement,
space zoom-out, Ukrainian detailed level; real-time frame rate in the built-in browser pane was not measured (the pane throttles it);
a frame costs about 5.5 ms when stepped manually.
