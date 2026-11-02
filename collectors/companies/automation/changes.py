"""Pure proposal validation and guarded private changes; never writes a profile."""
from dataclasses import dataclass, asdict
from datetime import date
import re

from .contracts import (ASSESSMENTS, CompanyView, ContractError, FieldValue, Policy,
                        ProposedClaim, ResearchReceipt, SECTIONS, aware, digest,
                        https_host, parse_json, require)


@dataclass(frozen=True)
class Issue:
    claim_ref: str
    code: str


@dataclass(frozen=True)
class HeldClaim:
    proposal: ProposedClaim
    assessment: str
    qa_status: str = "HOLD"
    public_release: bool = False

    def __post_init__(self):
        require(self.assessment in ASSESSMENTS and self.qa_status == "HOLD"
                and self.public_release is False, "PRIVATE_HOLD_REQUIRED")


@dataclass(frozen=True)
class Fill:
    field_path: str
    value_json: str
    supporting_hash: str


@dataclass(frozen=True)
class ChangeSet:
    company_id: str
    section: str
    base_fingerprint: str
    policy_hash: str
    claims: tuple
    fills: tuple
    issues: tuple
    coverage: str
    reviewed_at: str | None

    @property
    def completion_state(self):
        return "NEEDS_REVIEW" if self.issues else "SUCCEEDED"

    @property
    def fingerprint(self):
        claims = [[h.proposal.claim_ref, h.proposal.content_hash, h.assessment,
                   [[e.source.source_ref, e.source.url, e.source_hash, e.start, e.end,
                     e.binding, e.company_id] for e in h.proposal.evidence]] for h in self.claims]
        return digest([self.company_id, self.section, self.base_fingerprint, self.policy_hash,
                       claims, [asdict(f) for f in self.fills], [asdict(i) for i in self.issues],
                       self.coverage])


def evidence_error(claim, now):
    if not claim.evidence:
        return "SOURCE_MISSING"
    for e in claim.evidence:
        if e.company_id != claim.company_id:
            return "CROSS_COMPANY_EVIDENCE"
        if e.source_hash != e.source.content_hash:
            return "SOURCE_HASH_MISMATCH"
        if aware(e.source.captured_at) > now:
            return "CAPTURE_IN_FUTURE"
        if not (0 <= e.start < e.end <= len(e.source.text)) or e.source.text[e.start:e.end] != e.quote:
            return "QUOTE_NOT_IN_SOURCE"
        if e.observed != claim.qualifiers:
            return "QUALIFIERS_CONFLICT"
        if e.observed_field_path != claim.field_path or e.observed_value_json != claim.value_json:
            return "EXTRACTED_VALUE_CONFLICT"
    q = claim.qualifiers
    if q.as_of and date.fromisoformat(q.as_of) > now.date() and q.modality == "actual":
        return "FUTURE_ACTUAL_FACT"
    value = parse_json(claim.value_json)
    if "amount" in claim.field_path.split("/") and isinstance(value, (int, float)):
        if not q.currency or not q.unit or not q.period:
            return "AMOUNT_CONTEXT_MISSING"
    return None


def assessment(claim):
    kinds = {e.source.kind for e in claim.evidence}
    if kinds <= {"official_registry", "official_award"}:
        return "registry_supported"
    if "official_company" in kinds:
        return "company_claim"
    groups = {e.source.independence_group for e in claim.evidence if e.source.independence_group}
    hashes = {e.source_hash for e in claim.evidence}
    return "cross_source" if len(groups) >= 2 and len(hashes) >= 2 else "inferred_needs_review"


def evaluate(view, proposals, *, section, policy, now, research=None):
    require(isinstance(view, CompanyView) and isinstance(policy, Policy) and section in SECTIONS,
            "CHANGE_CONTEXT_REQUIRED")
    now = aware(now)
    proposals = tuple(proposals)
    require(len(proposals) <= policy.max_claims and all(isinstance(p, ProposedClaim) for p in proposals),
            "CLAIM_LIMIT_OR_TYPE_INVALID")
    require(len({p.claim_ref for p in proposals}) == len(proposals), "DUPLICATE_CLAIM_REF")
    sources = {(e.source.url, e.source_hash): e.source for p in proposals for e in p.evidence}
    require(len(sources) <= 80 and sum(len(s.text.encode("utf-8")) for s in sources.values()) <= 8 * 1024 * 1024,
            "SOURCE_BATCH_LIMIT")
    accepted, fills, issues, seen = [], [], [], set()
    fields = dict(view.fields)
    coverage = dict(view.section_states).get(section, "NOT_RESEARCHED")
    proposed_countries = {parse_json(p.value_json) for p in proposals
                          if p.field_path == "/identity/country" and isinstance(parse_json(p.value_json), str)}
    ua = view.country == "UA" or "UA" in proposed_countries
    for claim in proposals:
        reason = None
        if view.protected:
            reason = "RECORD_PROTECTED"
        elif claim.company_id != view.company_id or claim.section != section:
            reason = "CLAIM_SCOPE_MISMATCH"
        elif claim.field_path.split("/")[1] != section.lower():
            reason = "FIELD_SECTION_MISMATCH"
        elif claim.content_hash in view.rejected_hashes:
            reason = "REJECTED_CONTENT"
        elif claim.content_hash in seen:
            reason = "DUPLICATE_CONTENT"
        elif parse_json(claim.value_json) in (None, ""):
            reason = "UNKNOWN_OR_EMPTY_VALUE"
        elif claim.field_path.startswith("/geography/") and claim.field_path != "/geography/country" and (ua or not view.country):
            reason = "SENSITIVE_OR_UNRESOLVED_GEOGRAPHY"
        else:
            reason = evidence_error(claim, now)
        if reason:
            issues.append(Issue(claim.claim_ref, reason))
            continue
        seen.add(claim.content_hash)
        held = HeldClaim(claim, assessment(claim))
        accepted.append(held)
        if claim.field_path not in policy.fill_paths:
            continue
        old = fields.get(claim.field_path, FieldValue(False))
        if old.owner == "manual":
            issues.append(Issue(claim.claim_ref, "MANUAL_VALUE_PROTECTED"))
            continue
        if old.known:
            if old.value_json != claim.value_json:
                issues.append(Issue(claim.claim_ref, "NONEMPTY_VALUE_CONFLICT"))
            continue
        if held.assessment not in ("registry_supported", "company_claim"):
            issues.append(Issue(claim.claim_ref, "FILL_EVIDENCE_NOT_ADMITTED"))
            continue
        if claim.qualifiers.polarity != "positive" or claim.qualifiers.modality != "actual":
            issues.append(Issue(claim.claim_ref, "NONACTUAL_BASE_FILL"))
            continue
        value = parse_json(claim.value_json)
        try:
            if claim.field_path == "/identity/website":
                https_host(value)
            else:
                require(isinstance(value, str) and bool(re.fullmatch(r"[A-Z]{2}", value)), "COUNTRY_INVALID")
                require(not view.country or view.country == value, "COUNTRY_CONFLICT")
                require(value not in ("RU", "CN"), "EXCLUDED_COUNTRY")
        except ContractError as exc:
            issues.append(Issue(claim.claim_ref, str(exc)))
            continue
        previous = next((f for f in fills if f.field_path == claim.field_path), None)
        if previous and previous.value_json != claim.value_json:
            fills = [f for f in fills if f.field_path != claim.field_path]
            issues.append(Issue(claim.claim_ref, "BATCH_VALUE_CONFLICT"))
        elif not previous and not any(i.code == "BATCH_VALUE_CONFLICT" and
                                     next(p.field_path for p in proposals if p.claim_ref == i.claim_ref) == claim.field_path
                                     for i in issues):
            fills.append(Fill(claim.field_path, claim.value_json, claim.content_hash))
    reviewed = None
    if accepted:
        if coverage != "HAS_APPROVED_CLAIMS":
            coverage = "HAS_HOLD_CLAIMS"
        reviewed = now.isoformat()
    elif research is not None:
        require(isinstance(research, ResearchReceipt) and research.company_id == view.company_id
                and research.section == section, "RESEARCH_SCOPE_MISMATCH")
        if (not view.protected and not proposals and research.complete and research.no_results and research.checked
                and all(aware(s.captured_at) <= now for s in research.checked)):
            if coverage not in ("HAS_APPROVED_CLAIMS", "HAS_HOLD_CLAIMS"):
                coverage = "RESEARCHED_NOT_FOUND"
            reviewed = now.isoformat()
    return ChangeSet(view.company_id, section, view.fingerprint, policy.fingerprint,
                     tuple(accepted), tuple(fills), tuple(issues), coverage, reviewed)


def check_preconditions(changes, current, policy):
    """Future writer must call under its lock, in the SAME transaction as all writes."""
    require(isinstance(changes, ChangeSet) and isinstance(current, CompanyView), "CHANGESET_REQUIRED")
    require(changes.company_id == current.company_id and changes.base_fingerprint == current.fingerprint,
            "STALE_CONTEXT")
    require(changes.policy_hash == policy.fingerprint, "POLICY_CHANGED")
    require(not current.protected, "RECORD_PROTECTED")
    require(changes.section in SECTIONS and len(changes.claims) <= policy.max_claims, "COMMIT_SCOPE_INVALID")
    for h in changes.claims:
        require(h.qa_status == "HOLD" and h.public_release is False, "PRIVATE_HOLD_REQUIRED")
        require(h.proposal.company_id == current.company_id and h.proposal.section == changes.section
                and h.proposal.field_path.split("/")[1] == changes.section.lower(), "COMMIT_CLAIM_SCOPE_INVALID")
        require(h.proposal.content_hash not in current.rejected_hashes, "REJECTED_CONTENT")
    require(len({f.field_path for f in changes.fills}) == len(changes.fills), "DUPLICATE_FILL")
    for fill in changes.fills:
        old = dict(current.fields).get(fill.field_path, FieldValue(False))
        require(fill.field_path in policy.fill_paths and old.owner != "manual" and not old.known,
                "PROTECTED_FILL")
        require(any(h.proposal.content_hash == fill.supporting_hash and h.proposal.field_path == fill.field_path
                    and h.proposal.value_json == fill.value_json for h in changes.claims), "SUPPORTING_CLAIM_MISSING")
