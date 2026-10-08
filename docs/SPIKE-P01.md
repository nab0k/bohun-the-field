# P01: Hybrid architecture experiment

Architecture: MapLibre global geography → Phaser strategy sector → React commercial mission.

## Upstream reuse
- MapLibre GL JS: https://github.com/maplibre/maplibre-gl-js (BSD-3-Clause, preserve license notices)
- Phaser: https://github.com/phaserjs/phaser (MIT)
- Official React/Vite bridge pattern: https://github.com/phaserjs/template-react (MIT)
- Camera and interaction examples: https://github.com/phaserjs/examples

This spike implements a small original synthetic scene rather than copying a full RTS or proprietary game assets. It reuses the framework capabilities and React lifecycle bridge pattern, not commercial game sprites.

## Manual browser acceptance
1. Click UKRAINE on global map.
2. Observe camera fly-to and ENTER STRATEGY SCENE.
3. Click ENTER; a Phaser scene replaces the map, no reload.
4. Drag scene, wheel zoom; click MANUFACTURING.
5. Confirm React site panel opens; click I NEED SOMETHING.
6. Follow contact link or return to WORLD MAP.
7. Repeat transitions 5 times and inspect console/WebGL warnings.
8. Test keyboard accessibility and mobile. Known limitation: scene site selection is pointer-first; accessible DOM mirror controls are a follow-up.

## Scope and risks
- Synthetic schematic factory/logistics/technology nodes; not real facilities or coordinates.
- Flat top-down scene, not yet isometric asset art.
- No deck.gl; integrate only after verifying performance.
- No full real-time strategy gameplay, pathfinding or operational military data.
- CI build passing is necessary but not sufficient: browser smoke and visual QA remain required.
