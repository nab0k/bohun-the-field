# BOHUN Signals: qualification contract (draft v0.1)

Status: RESEARCH_PENDING / NOT FOR PUBLICATION
Date: 2026-10-31

## Purpose
Detect evidence-backed changes relevant to defence and adjacent industrial markets. A signal is not a news summary, a count of headlines, or an LLM prediction.

## Candidate definition
A signal candidate is a falsifiable, market-relevant hypothesis about an observed or emerging change, linked to dated, traceable source evidence and an explicit causal or operational mechanism. This is a provisional product definition pending the BOHUN Signals research.

## Pipeline boundary
source documents -> extracted evidence -> normalized events -> candidate hypotheses -> deterministic qualification gates -> analyst review -> signal monitoring.

Synthesis can propose hypotheses and provide context, but is not independent evidence of its underlying sources.

## Required gates (fail closed)
1. Evidence has stable document ID, source URL, retrieval and publication dates, source hash, and exact quote/span when available.
2. Distinguish source event time from publication time; never use later evidence to qualify a past signal.
3. Deduplicate syndicated reports using independent_origin_id, without collapsing distinct events in the same document.
4. Explain what changed, where, for whom, and why it matters.
5. Separate source facts, analyst inferences, uncertainties, and disconfirming evidence.
6. Record monitoring indicators and conditions that would weaken or falsify the hypothesis.
7. No automated public release until research-derived qualification rules and explicit approval exist.

## Signal object (conceptual)
- id; status (CANDIDATE | QUALIFIED | MONITORING | CONFIRMED | REJECTED | EXPIRED)
- subject (market, geography, technology, entities)
- claim, change_type, mechanism, horizon
- evidence_refs[], counterevidence_refs[], independent_origin_ids[]
- significance, novelty, evidence_quality, confidence (separate dimensions; not calibrated probabilities)
- observed_at, assessed_at, model_version, qualification_version
- monitoring_conditions[], revision_history[], publication_allowed=false

## Product boundaries
News = what happened.
Synthesis = what multiple sources collectively establish and imply.
Signals = what meaningful market change is hypothesized or established, on what evidence, and how its status evolves.

## First implementation slice
Build read-only candidate extraction from existing approved evidence and synthesis provenance; generate a structured candidate with references and a short Russian explanation. Store privately, status=CANDIDATE, publication_allowed=false. No new public feed, schema migration, production secret, or deploy in this slice.

## Open decisions
Research must decide necessary/sufficient criteria, taxonomy, lifecycle, scoring/calibration, treatment of single high-impact events, and thresholds. This draft must not be represented as a validated standard.
