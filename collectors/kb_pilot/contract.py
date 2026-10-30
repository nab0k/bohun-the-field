"""Contract check before the daily chain (D-20261030-DAILY-CHAIN): the chain reads other teams' tables only through
these columns. When one is missing the chain stops before writing anything and the feed stays as it is; adding
sources or companies does not touch the contract.
"""
import sys

from ..synthesis.contracts import canonical

COLUMNS = {
    "intelligence.items": ["id", "title", "url", "published_at", "raw_data"],
    "knowledge.item_embedding": ["item_id", "origin_item_id", "origin_method", "embedding"],
    "knowledge.item_processing": ["item_id", "pipeline_version", "outcome", "reason_codes", "cost_micro_usd"],
    "knowledge.node": ["id", "kind", "key", "name", "aliases", "status"],
    "knowledge.edge": ["src_node_id", "rel", "dst_node_id", "context_node_id", "status", "superseded_by", "row_hash"],
    "knowledge.event": ["id", "event_type", "title", "link_method", "first_published_at"],
    "knowledge.synthesis": ["id", "event_id", "active_version", "is_hidden"],
    "knowledge.synthesis_version": ["synthesis_id", "version", "headline", "what_happened", "sources", "generator"],
    "knowledge.synthesis_admission": ["synthesis_id", "version", "decision", "reason_codes", "public_release"],
    "knowledge.topic": ["id", "slug"],
    "companies.company": ["id", "name"],  # read only for links at export time; never needed to write a synthesis
}
FUNCTIONS = ["public.export_synthesis_feed"]


def missing(cur):
    out = []
    for table, cols in COLUMNS.items():
        schema, name = table.split(".")
        cur.execute("""SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s""",
                    (schema, name))
        have = {r[0] for r in cur.fetchall()}
        out += ["%s.%s" % (table, c) for c in cols if c not in have]
    for fn in FUNCTIONS:
        cur.execute("SELECT to_regproc(%s) IS NOT NULL", (fn,))
        if not cur.fetchone()[0]:
            out.append(fn + "()")
    return out


def main():
    from ..synthesis.live import connect
    with connect() as conn:
        with conn.cursor() as cur:
            gaps = missing(cur)
    print("KB_CONTRACT " + canonical({"ok": not gaps, "missing": gaps}))
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
