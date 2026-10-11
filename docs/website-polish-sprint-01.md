# Website Polish Sprint 01 — evidence-first audit
Checked 2026-10-11 UTC. State: REVIEW ONLY, NO DEPLOY.

## Source of truth, an important mismatch
- Development repository `nab0k/bohun-the-field` at `main`: `index.html` and `atlas.html` are older prototype source entries. Old labels remain in these files. They are **not** direct proof that the current production UI shows these labels.
- Published repository `nab0k/bohundefense` at `main`: `index.html` is already the modern Bohun Defence Intelligence Map shell; it loads bundled `/assets/atlas-Brcz3Unr.js` and `/assets/atlas-QXBP-sMt.css`. Brand logo and old journey buttons hidden. This is the correct target for deployment verification. Do **not** overwrite published site with raw development index.
- Production HTML has main/header/menu, map, `#side` search/layers, `#ind-card` company dossier, old hidden journey `#panel`. No visible permanent separate left Intelligence Feed container was found in the current published HTML. Test in real browser before treating JS-injected panels as absent.
- Development `src/atlas/main.js` still refers to `/atlas/companies-at.json`; other-country vetted company map loading is not proven.
- Direct independent page fetch via web reader failed. Browser screenshots, interaction tests, DNS/checks and deployed CSS visuals remain NOT VERIFIED.

## P0: changes that need no AI publication
1. Navigation: retain existing logo and brand, About, Services, About Project, Support Project, Contact; preserve real contact form. Test mobile hamburger open/close, keyboard focus, tab ordering, ESC behavior, viewport width 375/768/1440. Logo origin in production HTML is absolute `https://bohundefence.com/logo_gold.png` and could be converted to root-relative only after confirming backup asset and no redirect regressions.
2. Correct legacy control clutter: visible top-level NIGHT/INDUSTRY/UNDERSEA/COMPANIES/WORLD/RESTART and duplicated layer controls should be reorganized into one accessible drawer, with selected states synchronized with map layers. Keep Night Side only at far zoom; Light Minimal only. Do not erase the thematic layers.
3. Company dossier: use official source/link, checked date, role/HQ-city, legal name, disclosed product/capability, unknowns. Hide empty sections, never use guessed entity IDs, addresses, ownership or vendor status.
4. Empty states: clearly separate `official source index` from `AI analysis QA-approved`, and `no vetted company data here yet` from `no companies exist`. Do not publish HOLD candidate text or fake counters.
5. Baseline accessibility/performance: one H1 or accessible primary content landmark, bypass navigation to Intelligence Map, keyboard navigation for layer toggles, focus-trapping only when drawer open, respect reduced motion, test wide/mobile and loading/error fallback, avoid CSS overlap.
6. SEO/legal: root meta description, sensible page titles, on-page provenance/coverage disclaimer, links to official sources, contact privacy/disclosure as applicable.

## P1: gated enhancements
- Three-pane composition: LEFT persistent independently scrolling verified source feed; CENTRAL existing MapLibre globe; RIGHT contextual event/company dossier. Desktop 1440 wide, adapt 768 tablet and 375 mobile bottom-sheet/drawer. Do not obscure map or old commercial pages.
- Source-only records may appear only if rights and provenance checks pass and explicitly labeled as source index; AI commentary only when QA08 APPROVE and calibrated gates.
- 2-country company records only after Company03+QA08 accepted ID exports. HQ_CITY for public vetted city, COUNTRY_ONLY otherwise, NONE when no reliable geographical locus. Never fake point precision.
- Search: make limits/coverage clear and consistent with actual loaded datasets; avoid promising global company coverage with Austria-only overlay.

## Release gates
- Obtain genuine browser screenshot desktop+mobile from deployment candidate, not a generated mockup.
- CI build + static audit + mobile interaction checks and click-through company/search/source.
- Verify actual root/public asset linkage and contact form unchanged; ensure backup+rollback.
- Verify no new published HOLD/REJECT AI cards and no unapproved database mutation.
- Separately approve merge/deployment to `nab0k/bohundefense`; current branch is audit only.

## Owner/stream handoffs
Engineering09: choose actual current production source template, prepare branch with the 3-pane skeleton and accessibility fixes. Companies03: provide two-country accepted company records. QA08: approve source-only/AI feed classification; Sources02: provide source manifests. HQ01: own release gate and canonical mapping of deployment repo.
