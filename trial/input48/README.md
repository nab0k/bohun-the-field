# BOHUN INPUT48: isolated analytical-core trial

This directory is a candidate for one separately approved cloud trial. It does
not install the production pipeline, publish analytics or change the website.

`code-only.zip` contains the pinned runner and its runtime dependencies. It
contains no news documents, editorial answers, private company records, API
tokens or private keys. `code-manifest.json` lists the individual source hashes.
The archive SHA256 is:

```
1242e3d4de8a56a05dc1fc49a80fbe0cf12e181689a24964235d91fadd209ad4
```

The workflow accepts only the repository owner's approved PR-label event and
checks the exact PR head, code bundle, source hashes and recipient certificate.
The public authorization manifest belongs in the PR description after the head
commit exists; committing a manifest containing its own commit SHA would be
self-referential. Use exactly one block:

```
BOHUN_INPUT48_APPROVAL_BEGIN
<separately reviewed JSON authorization manifest>
BOHUN_INPUT48_APPROVAL_END
```

Execution additionally requires the existing `CF_AI_TOKEN`, a separately approved
temporary `EXP009_INPUT48_PACKET_B64` data container and an approved public
recipient certificate. The private decryption key stays off GitHub. Creating a
branch or PR does not authorize supplying secrets or starting the trial.

Limits: six fixed cases, three stages each, at most 18 generation attempts,
no automatic retries or provider fallback, FREE_ONLY, and the original cumulative
budget cap. Unknown spend/account status, invalid evidence, incomplete history
or provider errors stop execution. Paid-equivalent accounting is identified as
an estimate and never presented as a verified invoice.

Only encrypted receipts may be uploaded. Drafts remain HOLD with public release
disabled. There is no schedule, website deployment, Notion or database write.

Local validation: 114 offline tests passed, including 23 cloud-adapter tests.
These used fictional responses; they do not demonstrate model quality, current
API access or successful execution on GitHub. Human fact checking and editing
time measurements remain separate.
