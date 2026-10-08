# Sprint 00 / Foundation

Issue: #1. This PR establishes the deployable foundation and first interactive map.

## Ready now
- Responsive HERO with approved headline and two primary user intents.
- Six illustrative signals at representative country/city coordinates.
- Clickable map markers, signal detail panel, intent preview and contact link.
- MapLibre raster base map using CARTO Dark Matter and OSM attribution.
- GitHub Actions typecheck/build and GitHub Pages deployment after merge.

## Explicitly not implemented
- No verified live requests, tenders, shipments or military intelligence.
- No animated transport, satellites or actual route data.
- No full three-step gameplay or backend form submission yet.
- No production-domain cutover.

## Manual review
1. Open the GitHub Pages preview after merging to main and enabling Actions Pages source.
2. Confirm all six country markers open the correct detail.
3. Confirm two primary buttons open different mission panels.
4. Check mobile and keyboard navigation.
5. Review attribution and visual design.

## Integration audit (planned)
Evaluate deck.gl for arcs, GSAP for UI transitions, Natural Earth for offline outlines, and Playwright for browser smoke tests. Review licenses and bundle impact before adding.

## Safety
All signals are synthetic demonstrations. Do not insert customer RFQs, military unit positions, actual routes, or confidential transaction data.
