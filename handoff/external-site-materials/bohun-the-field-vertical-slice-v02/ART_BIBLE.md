# BOHUN / THE FIELD — Art Bible v1

## 1. Visual proposition
An original early-2000s isometric RTS commercial field: strategic, restrained, tactile and legible. It should feel game-native but never read as combat, parody, fake intelligence theatre or a generic defence contractor interface.

## 2. Camera + grid
- 2:1 isometric projection.
- Base production tile: 96×48 px at 1× logical scale.
- Structures occupy 1–3 tiles; Bohun is visually dominant but not monumental.
- Presentation camera uses three authored scales: overview 0.85–1.0, inspection 1.15–1.3, dossier focus 1.25–1.45.
- Mobile uses the same world with a constrained authored path, not a miniature desktop map.

## 3. Material language
Terrain: desaturated olive/earth, low-contrast tile variation, occasional industrial pads and water/river geometry.
Structures: concrete, dark oxidised metal, muted timber and pale industrial roofs. No camouflage skins.
Routes: fine sand/gold dotted or dashed lines, brighter only on active match/reveal.
Signals: warm amber pulse, distinct from danger/red semantics.

## 4. Palette
- Field black: `#151710`
- Deep panel: `#1D2118`
- Terrain olive: `#2A3021`
- Industrial pad: `#454636`
- Paper: `#D8D4BD`
- Bohun gold: `#D8C07B`
- Active signal: `#E6CF80`
- Muted interface: `#A6AA91`
- Caution only: `#B7765F`

Gold is navigation/activation, not luxury decoration.

## 5. Entity differentiation
- **BOHUN:** compact command/coordination structure, bow/arrow-derived mast or marker.
- **SUPPLIER:** workshop/factory geometry, green-olive accent.
- **BUYER:** restrained institutional/warehouse compound, earth accent. Never badges, flags or government seals.
- **PARTNER:** bridge/junction-like structure or dual-volume building.
- **UKRAINE:** geographic/market node, subtle contemporary Ukrainian cue only. Avoid folk/touristic motifs.
- **INTERNATIONAL MARKET:** grouped institutional/industrial geometry, cooler desaturated accent.
- **CAPABILITY SIGNAL:** abstract beacon/marker, never a weapon icon.

## 6. Sprite taxonomy
`terrain`, `road`, `water`, `industrial-pad`, `bohun-node`, `supplier-node`, `buyer-node`, `partner-node`, `factory`, `workshop`, `warehouse`, `communications`, `industrial-facility`, `generic-vehicle`, `generic-person`, `signal`, `route`, `document`, `capability-marker`, `ukraine-marker`, `market-marker`.

## 7. Sprite states
Every interactive entity supports: `idle`, `hover`, `selected`, `active`, `dimmed`, `revealed` where applicable.
Animation remains modest: 2–6 px movement, slow rotor/antenna, pulse rings, route drawing, tiny unit travel. No spectacle.

## 8. Typography
- Primary editorial/UI: contemporary sans serif, high legibility.
- Monospace for file IDs, coordinates, status and compact HUD labels.
- Pixel/bitmap faces are optional for tiny decorative labels only and must never carry substantive copy.
- Avoid stencil, military display and faux-classified type.

## 9. Dossier UI
Dossiers are semantic HTML with a paper/field-document material system. They use file ID, type, category and status, but never fake classification stamps. High contrast, readable paragraphs, explicit CTA and compliance microcopy.

## 10. Interaction grammar
`MAP → SIGNAL → FILE → MATCH → CONTACT`
- Hover = identify.
- Click = inspect/open file.
- Scroll in Presentation Mode = authored camera move.
- Reveal in Game Mode = information or route appears.
- Match = a route becomes legible, not a transaction-complete animation.

## 11. Mobile translation
- One focal object per camera state.
- Bottom content panel instead of left/right desktop composition.
- Intent buttons stack.
- Game Mode is optional and may reduce available pan/zoom range.
- All commercial text and contact actions remain usable without canvas interaction.

## 12. Prohibited visual language
No combat, explosions, crosshairs, weapon selectors, health bars, camouflage cliché, Soviet styling, CIA styling, TOP SECRET theatre, fake operational feeds, fake government access, fake procurement notices or fake buyer activity.

## 13. Consistency checklist
Before adding an asset ask:
1. Does it represent a real information/navigation role or subtle ambience?
2. Does it fit 2:1 isometric geometry and established scale?
3. Does it use the field/material palette rather than a one-off illustration palette?
4. Can it support idle/hover/selected without redrawing its whole visual language?
5. Is the historical/Bohun reference subtle and original?
6. Does the asset avoid implying combat or a real procurement event?
7. Is substantive meaning still available in HTML/accessibility layers?
