ALTER TABLE knowledge.synthesis_review_retry
    DROP CONSTRAINT synthesis_review_retry_reason_check,
    ADD CONSTRAINT synthesis_review_retry_reason_check
        CHECK (reason IN ('reviewer_unavailable', 'missing_evidence', 'rate_limited', 'daily_quota', 'billing_blocked'));

CREATE TABLE knowledge.synthesis_review_route_block (
    provider text PRIMARY KEY,
    reason text NOT NULL CHECK (reason IN ('rate_limited', 'daily_quota', 'billing_blocked')),
    resume_at timestamptz,
    CHECK ((resume_at IS NULL) = (reason = 'billing_blocked'))
);

REVOKE ALL ON knowledge.synthesis_review_route_block FROM PUBLIC, anon, authenticated;
