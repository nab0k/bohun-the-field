-- Apply before deploying the worker that records short deferrals for exhausted web search.
begin;
set local lock_timeout = '2s';
alter table companies.website_attempt
  drop constraint website_attempt_outcome_check,
  add constraint website_attempt_outcome_check
    check (outcome in ('found', 'not_found', 'error', 'waiting_for_search'));
commit;
