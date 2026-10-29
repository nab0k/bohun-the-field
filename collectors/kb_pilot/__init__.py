"""EXP-028 knowledge-base pilot: one event → archive + search → dossier rows, documents → synthesis.

Isolated from the production synthesis route: reads intelligence.items and knowledge.* and writes
nothing to the database. The result is one JSON file plus Markdown documents for owner review.
"""
PILOT_VERSION = "kb-pilot-v1"
