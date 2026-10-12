"""Bohun Evidence Pipeline, Phase 1 (experimental, HOLD-only).

SOURCE -> FULL DOCUMENT -> TEXT EXTRACTION -> CLAIM EXTRACTION -> EXACT SPAN
ALIGNMENT -> SEMANTIC ENTAILMENT -> EVIDENCE RECORD -> EVALUATION / HOLD

Nothing in this package writes to Supabase, publishes, or changes the existing
Inoreader -> Gemini staging pilot. Every record it produces has
verification_status == "HOLD" and public_release == False by construction.
"""

PIPELINE_VERSION = "evidence-phase1-v0.1"
