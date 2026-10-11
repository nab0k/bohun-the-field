# BOHUN / THE FIELD — coded vertical slice v0.1

A self-contained interactive proof for the commercial `.com` direction.

## Run
From this folder:

```bash
python3 -m http.server 8080
```

Then open `http://localhost:8080`.

No build step or external dependency is required for this spike.

## What is implemented
- Real-time isometric canvas world with terrain, routes, nodes, ambient moving unit and signal.
- Presentation Mode as default.
- Scroll camera journey: overview → Current Interest → Bohun.
- `I NEED SOMETHING` / `I HAVE SOMETHING` changes emphasis in the same world.
- Category-level `PROPULSION` current-interest signal.
- Semantic HTML dossier and safe public-facing capability-interest copy.
- Experimental Game Mode with free pan/zoom, node selection, RESEARCH/REVEAL and a route appearing after interaction.
- Responsive/mobile camera and content-panel behaviour.
- Reduced-motion support and HTML/no-script fallback.
- Data model separated in `world-data.json`.

## Production handoff
See `TECH_ARCHITECTURE.md` for the Next.js + React + Phaser production recommendation and `ART_BIBLE.md` for the canonical visual rules.
