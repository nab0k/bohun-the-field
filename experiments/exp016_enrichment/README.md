# EXP-016 — original Python APIs, cloud native smoke

Prepared package only. **Cloud execution: NOT_RUN.** This is an isolated synthetic
test bench, not a production company collector, registry or enrichment service.
No installation or native/build-helper execution on the owner's Mac is permitted.

The existing local prototype supplies the two queues, immutable baseline, scoped
identity, field planning, receipts, observations, guarded fill/HOLD, disposable
SQLite copy and before/after reporting. All input companies and URLs are invented.
Private Notion/Supabase snapshots, old results and virtual environments are absent.
Names/scores do not confirm identity; company claims retain attribution. QA,
HOLD/REJECT/public_release and manual edits cannot be promoted or overwritten.
Literal text alignment is not semantic proof: text claims require semantic review.

## Proposed execution after separate integration/run approval

The only workflow is `.github/workflows/exp016-enrichment-native.yml`:
manual `workflow_dispatch`, public `nab0k/bohun-the-field`, main only, one standard
Ubuntu24.04 job, CPython3.13.17, 30-minute timeout, `contents: read`. No cron,
matrix, larger runners, service secrets, artifact upload or dependency cache.

Inputs are an independently approved full code SHA and the literal confirmation
`SYNTHETIC_NATIVE_ONLY`. The workflow must first be integrated into the default
branch through separately approved Git operations. Merely preparing these files
does not authorize push, merge or dispatch.

`cloud_environment.py` refuses execution outside the admitted GitHub Linux
context before creating files or starting processes. Its future actions:

1. `prepare`: check exact checkout/runtime, image compiler and ICU; create a new
   ephemeral venv; install setuptools80.9.0 from its pinned wheel/hash; download
   and verify PyICU2.17 source; check backend requirements; build only its wheel
   without build isolation/index; resolve other packages as wheels; validate the
   complete lock and install exactly its versions/hashes; `pip check`/freeze.
2. `verify`: use that venv for strict core/replay/native tests. All four native
   test cases must run and pass with zero skips. Then compare an actual repeated
   synthetic run and perform a separate four-component native smoke.
3. `summary`: publish technical results, versions, lock/hash and bounded failure
   logs to the job log/summary. Synthetic HTML/SQLite stay on the temporary runner.

Pins are followthemoney4.11.2, nomenklatura4.17.3, trafilatura2.3.1,
langextract1.7.1. Only PyICU2.17 may be built from source. Missing compiler/ICU,
unapproved build requirements, changed pins, absent wheels, bad hashes, unknown
index, any skipped native test or a replay mismatch stops the run. No apt,
Homebrew, ICU build, alternative Python/package versions or fallback algorithms.

## What the native tests prove

- FtM original Statement and StatementEntity provenance/serialization roundtrips.
- Nomenklatura original match/score/suggestions, followed by explicit synthetic
  judgement and expand; no automatic name-based identity merge.
- Trafilatura original extract through the existing hash-guarded web helper;
  raw legal identity is retained separately from main text.
- LangExtract original extract/resolver with a fixed fixture provider; fuzzy and
  lesser matching disabled. Literal spans/negative attributes are inspected;
  actual model transport calls remain zero. The provider is not an AI analysis.

`sources.py` resolves the checkout root from the package path and verifies the
entire imported project closure against fixed reference
`defa9c291d71da81b3363309e1d2be547675b1ce` before import. Before integration,
recheck it against the current main; never replace a dirty working tree blindly.

## Preparation checks and limitations

The preparation scope permits syntax/YAML/shell/static checks and the existing
pure-core test class using an already installed interpreter. Linux-only helper
contracts, project replay and all native tests are deferred to the cloud. Missing
packages or skipped tests must not be reported as native algorithm success.

Real companies30+10, discovery of real sources, actual extraction quality, model
quota, closed cloud storage, database/Notion/site writers and daily automation
remain separately approved later stages. No production Python module imports
this experimental package. This package contains no live/model/deploy mode.
