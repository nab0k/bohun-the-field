# BOHUN / THE FIELD — Technical Architecture Recommendation v0.1

## Decision
Production stack should remain **Next.js + React + Phaser**, with semantic HTML/CSS overlays and GSAP/ScrollTrigger only where it materially simplifies Presentation Mode camera choreography.

The included vertical slice is intentionally dependency-free so it can be run and QA'd immediately in a constrained environment. It validates the interaction contract, shared world model, scroll choreography, state switching, dossier semantics, mobile camera behaviour and lightweight game mechanics. It is a reference implementation, not a recommendation to replace Phaser with hand-written canvas code in production.

## Why Phaser remains the right world engine
- A genuine scene graph, camera, input, tweens, sprite animation, tilemaps and asset loading are needed once the world expands beyond this slice.
- Presentation Mode and Game Mode can share the same scene and entity data instead of becoming two products.
- React should own substantive content, forms, navigation, accessibility and SEO. Phaser should own terrain, sprites, world routes, ambient animation, signals, selection and the game camera.
- World state should live in a framework-agnostic store/data layer so both React overlays and Phaser scene consume the same canonical entities.

## Production modules
1. `app/` — Next.js routes, metadata, forms, server-side content.
2. `world/FieldScene.ts` — Phaser scene and camera.
3. `world/entities.ts` — entity schema and render factories.
4. `world/routes.ts` — route graph / reveal state.
5. `world/content-map.ts` — entity → HTML dossier/content references.
6. `state/field-store.ts` — intent, selected entity, mode, revealed routes.
7. `components/Dossier.tsx` — semantic HTML file overlay.
8. `components/IntentSwitch.tsx` — I NEED / I HAVE state.
9. `components/PresentationDirector.tsx` — scroll → camera target mapping.
10. `content/` — CMS/static content kept separate from render logic.

## Shared data contract
Each world entity requires at minimum:
`id, type, label, category, region, status, position, interaction, contentReference, visibleInPresentation, visibleInGameMode`.

No commercial concept should be hard-coded inside animation code. Adding a future capability signal should be a data/content operation, not a rendering-engine rewrite.

## Performance rules
- Server-render hero copy and CTAs independently of the game bundle.
- Dynamically load Phaser after first meaningful content.
- Atlas by visual family, not one giant sprite sheet.
- Lazy-load non-visible regions and optional Game Mode assets.
- Respect reduced motion.
- Cap effective DPR for the game canvas on high-density mobile screens.
- Maintain a non-canvas contact path and HTML content fallback.

## Risks found in spike
1. Scroll-directed camera and free-camera game control need a hard mode boundary to avoid input conflict.
2. Labels should remain HTML where possible at mobile sizes; canvas labels degrade quickly under zoom.
3. Large isometric worlds need deliberate occlusion and hit-area rules.
4. Asset consistency is a bigger risk than rendering technology. Art Bible and atlas discipline must precede scale-up.
5. Public 'interest' objects need content governance so CMS edits cannot accidentally turn them into detailed RFQs.

## Porting this slice to Phaser
Reproduce the same state machine first, not the exact canvas code:
`overview | need | have | current-interest | bohun | game`.
Then port tile rendering, entity factories, routes, ambient unit and camera targets. Keep the HTML dossier and copy unchanged.
