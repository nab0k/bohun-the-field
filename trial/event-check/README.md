# BOHUN isolated event-check trial

This is a candidate CPU validation workflow for GLiNER event extraction and comparison. It is not a production news service or a publication workflow.

The code-only archive contains the executor, narrowly adapted runtime helpers, and a frozen Linux dependency lock. The model is gliner2 2.1.0 with fastino/gliner2.5-multi-v1 revision cf5593a5d45e3bbf204b9df621b13b1c0cf25ee3. Model weights, input documents, evaluation labels, caches, environments and private keys are not stored here.

A run requires a separately approved exact head, owner-only label, unexpired approval block, and matching bundle, certificate and input hashes. The runtime rejects unknown history or repeated trial attempts. No schedule is defined.

Public-source inputs are delivered through a temporary data container only for an approved attempt. Evaluation labels remain local to the owner. Model outputs and diagnostics are encrypted with CMS AES-256-GCM before numbered ciphertext is written to the run log. Public logs do not provide private storage. Receiving the result requires binding the fetched log to the approved run and commit, in addition to verifying cryptographic integrity.

The trial uses CPU-only dependencies, two threads, bounded downloads, memory and execution time. It performs an author reference followed by document extraction and group comparison. A reference failure stops news processing. The domain adaptation and event-identity quality remain under evaluation; software checks are not scientific validation.

All results remain HOLD with public_release=false. No model API credentials, production database changes, Notion writes or site publishing are part of this workflow.
