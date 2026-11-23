CREATE TABLE knowledge.synthesis_review_retry (
    synthesis_id bigint NOT NULL,
    version integer NOT NULL,
    reason text NOT NULL CHECK (reason IN ('reviewer_unavailable', 'missing_evidence')),
    first_deferred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    next_attempt_at timestamptz NOT NULL,
    PRIMARY KEY (synthesis_id, version)
);

REVOKE ALL ON knowledge.synthesis_review_retry FROM PUBLIC, anon, authenticated;
