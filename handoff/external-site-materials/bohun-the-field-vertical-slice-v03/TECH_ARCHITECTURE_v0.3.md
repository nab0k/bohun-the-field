# BOHUN / THE FIELD — Technical Architecture v0.3

## Prototype
Self-contained Canvas 2D + semantic HTML/CSS so the interaction model can be run without external dependencies.

## Production target
- Next.js / React shell
- Phaser world renderer and input/state system
- semantic React overlays for navigation, dossiers, forms, SEO and accessibility
- shared structured world/content data
- GSAP/ScrollTrigger only where useful for controlled Presentation Mode camera choreography

## State model
`intent`: need | have
`mode`: presentation | game
`selectedEntity`
`scenarioStage`: signal → researched → dispatched → connected

World entities remain data-driven. Gameplay actions update world state, which controls reveal, movement, route rendering, dossier availability and minimap state.

## Next technical spike after visual approval
1. Move map/entity/state model into Phaser scene.
2. Replace canvas placeholder structures with canonical sprite atlas.
3. Add pathfinding along authored roads for liaison/ambient units.
4. Add scalable region/asset streaming for full homepage world.
5. Retain HTML overlays and accessible non-game fallback.
