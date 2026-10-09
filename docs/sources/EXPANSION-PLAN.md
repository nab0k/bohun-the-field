# Global source expansion batch 1 (2026-10-09)

**84 candidate source records**, in `global-source-candidates-20261009.csv`, across media, government, industry, grants, procurement, analysis and events. These are *candidate official/homepage URLs from a curated seed list*, **not live-checked feeds**. They are not automatically subscribed, ingested, or published.

## Quality gate
1. Verify that the site resolves and is the authentic publisher.
2. Find and test a real RSS/Atom feed or official API, or assign a respectful web adapter.
3. Identify a *specific* news, press, opportunities or tender listing endpoint rather than blindly scanning the homepage.
4. Record publication date, country, organization, source URL, and update frequency.
5. Deduplicate by canonical URL and issuer/programme identifiers; keep grant/tender statuses and deadlines.
6. Respect robots.txt, access controls and rate limits. Do not infer an API or RSS feed merely from the homepage.
7. Cross-check against existing 42 Inoreader subscriptions and 12 Web Monitor seeds before importing. This batch includes intentional overlaps to provide a single global inventory.
8. Promote `candidate_unverified` to `verified` only after successful test fetch and manual sample audit.

**Do not enable `--apply` on the existing Web Monitor**: its candidate URL filter does not validate publication pages.

## Implementation order
- P1: official grants and procurement portals (structured collection), defence media and industry pressrooms (verified RSS first).
- P2: think tanks, conferences and ecosystem sources.
- Telegram @Defencegrantbot and @DUgrants are secondary discovery signals, not substitutes for official programme URLs.
