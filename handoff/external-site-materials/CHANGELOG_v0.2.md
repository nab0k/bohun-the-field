# BOHUN / THE FIELD — v0.2 Visual Gate Pass

## What changed

1. Fixed presentation-camera drift by changing scene focus from cumulative offsets to absolute camera targets.
2. Made the field inspectable in Presentation Mode: clicking/tapping a node opens its dossier without requiring Game Mode.
3. Turned I NEED SOMETHING / I HAVE SOMETHING into route states, not only node dimming. Relevant routes and entities now stay prominent while the opposite side recedes.
4. Clarified the separation between Presentation Mode and Game Mode. Presentation is browsable; Game Mode adds free camera, selection and RESEARCH / REVEAL.
5. Added a compact field legend and stronger Current Interest / dossier visual grammar.
6. The runtime now attempts to read world-data.json as the shared canonical world model, with a fallback for locked-down file previews.
7. Added type-level visual differentiation for supplier, buyer, partner, market, Bohun and signal nodes.
8. Tightened mobile presentation behavior and hero overflow handling.

## Gate assessment

The concept remains viable, but this is still an interaction/architecture proof rather than a production-quality art pass. The biggest unresolved variable is art quality: terrain, structures, sprites, route treatment and dossier typography need a deliberately authored sprite system before the result can be judged as a premium commercial website rather than a competent prototype.

## Next production gate

Do not add homepage sections 04–10 yet. First produce the canonical reference scene and first real asset kit under Art Bible v1.1, then replace geometric placeholder buildings with those assets while preserving the v0.2 interaction model.
