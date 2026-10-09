# THE COALITION · Checkpoint 01
Date: 2026-10-09

## Verified repository
- `atlas.html` uses `/src/atlas/main.js` and `/src/atlas/atlas.css`.
- `field.html` uses `/src/field/field.css`.
- `vite.config.js` builds `atlas.html` as an entry page.
- `package.json` uses Vite 8, MapLibre 6, Phaser 3 and existing test scripts.
- The atlas already contains INDUSTRY, UNDERSEA INFRASTRUCTURE and LIVE controls.

## Implementation in this branch
- `src/coalition/engine.js`: independent immutable weekly campaign state and queued project decisions.
- `scripts/test-coalition.mjs`: deterministic smoke assertions.
- All values are illustrative game parameters. Organisations are referenced by IDs, never copied into game state.

## Not yet complete
- EXPLORE / PLAY control and map-side panel integration.
- Loading actual verified organisation IDs from the industry dataset.
- Browser smoke test, Vite build and gameplay verification.
- Saving campaigns and scenario authoring.

## Integration contract
Keep real-world datasets read-only. Store hypothetical campaign state separately. The existing map and industry layer remain authoritative for location and organisation facts. Do not deploy from this branch without review.
