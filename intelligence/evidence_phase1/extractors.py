"""Claim extractors behind one interface.

Both variants return the same candidate dicts so the rest of the pipeline is
identical: {claim_text, evidence_text, char_start?, char_end?, asserted_by,
modality, event_date, extractor_alignment?}. Extractors do not decide status.

- GeminiDirectExtractor: same REST call style as scripts/inoreader-gemini-staging-pilot.py,
  but asks for several claims with modality/attribution/date fields.
- LangExtractExtractor: google/langextract with fuzzy and partial alignment DISABLED,
  so char_interval is only set for exact matches. It still needs an LLM.
"""
import json
import os
import time
import urllib.request

INSTRUCTION = (
    "Extract up to {max_claims} factual statements about defence or dual-use industry "
    "(contracts, awards, deliveries, funding, partnerships, production, tests, policy) "
    "that the text makes. For each, copy evidence_text VERBATIM as one contiguous span of the "
    "input (no paraphrase, no ellipsis, no added punctuation). claim_text is a short standalone "
    "restatement that must not add any fact absent from the span. modality is one of: occurred, "
    "announced, planned, negated, cancelled, speculative, unknown. asserted_by is the organisation "
    "or person that makes the statement in the text (e.g. the company in its own press release, "
    "or 'Reuters citing officials'); null if not stated. event_date is the date of the event itself "
    "as YYYY, YYYY-MM or YYYY-MM-DD ONLY if the text states it; never use the article's "
    "publication date; null otherwise. Do not use outside knowledge. Return JSON only."
)

SCHEMA_HINT = {
    "claims": [{
        "claim_text": "string",
        "evidence_text": "verbatim span",
        "modality": "occurred|announced|planned|negated|cancelled|speculative|unknown",
        "asserted_by": "string or null",
        "event_date": "YYYY[-MM[-DD]] or null",
    }]
}


class ExtractorUnavailable(RuntimeError):
    pass


class GeminiDirectExtractor:
    name = "gemini-direct"

    def __init__(self, model=None, max_claims=8, max_chars=12000, max_output_tokens=2048, transport=None):
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.max_claims = max_claims
        self.max_chars = max_chars
        self.max_output_tokens = max_output_tokens
        self._transport = transport  # tests inject a fake; production uses urllib

    def _post(self, body):
        if self._transport:
            return self._transport(body)
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ExtractorUnavailable("GEMINI_API_KEY_NOT_SET")
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            data=body, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.load(resp)

    def extract(self, text):
        bounded = text[: self.max_chars]
        prompt = {
            "instruction": INSTRUCTION.format(max_claims=self.max_claims),
            "required_shape": SCHEMA_HINT,
            "text": bounded,
        }
        body = json.dumps({
            "contents": [{"parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "maxOutputTokens": self.max_output_tokens, "temperature": 0},
        }).encode()
        t0 = time.perf_counter()
        result = self._post(body)
        latency = time.perf_counter() - t0
        raw = result["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw)
        claims = parsed.get("claims") if isinstance(parsed, dict) else None
        if not isinstance(claims, list):
            raise ValueError("MODEL_OUTPUT_HAS_NO_CLAIMS_LIST")
        usage = result.get("usageMetadata") or {}
        return {
            "candidates": [dict(c) for c in claims[: self.max_claims] if isinstance(c, dict)],
            "meta": {
                "extractor": self.name, "model": self.model, "latency_s": round(latency, 3),
                "input_chars": len(bounded), "truncated": len(text) > len(bounded),
                "prompt_tokens": usage.get("promptTokenCount"),
                "output_tokens": (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0) if usage else None,
                "token_usage_source": "provider_usageMetadata" if usage else "NOT_MEASURED",
            },
        }


EXAMPLE_TEXT = (
    "Northwind Aero said on Tuesday it had signed a contract with the Ruritanian Air Force "
    "for 12 trainer aircraft, with deliveries planned to begin in March 2027. The company "
    "denied reports that a second order had been placed."
)


def _langextract_examples(lx):
    return [lx.data.ExampleData(
        text=EXAMPLE_TEXT,
        extractions=[
            lx.data.Extraction(
                extraction_class="claim",
                extraction_text="it had signed a contract with the Ruritanian Air Force for 12 trainer aircraft",
                attributes={"claim_text": "Northwind Aero signed a contract with the Ruritanian Air Force for 12 trainer aircraft.",
                            "modality": "announced", "asserted_by": "Northwind Aero", "event_date": None},
            ),
            lx.data.Extraction(
                extraction_class="claim",
                extraction_text="deliveries planned to begin in March 2027",
                attributes={"claim_text": "Deliveries of the trainer aircraft are planned to begin in March 2027.",
                            "modality": "planned", "asserted_by": "Northwind Aero", "event_date": "2027-03"},
            ),
            lx.data.Extraction(
                extraction_class="claim",
                extraction_text="denied reports that a second order had been placed",
                attributes={"claim_text": "Northwind Aero denied that a second order had been placed.",
                            "modality": "negated", "asserted_by": "Northwind Aero", "event_date": None},
            ),
        ],
    )]


class LangExtractExtractor:
    name = "langextract"

    def __init__(self, model_id=None, max_char_buffer=1500, extraction_passes=1, model=None):
        try:
            import langextract as lx  # noqa: F401
        except ImportError as exc:
            raise ExtractorUnavailable("LANGEXTRACT_NOT_INSTALLED") from exc
        self.lx = lx
        self.model_id = model_id or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.max_char_buffer = max_char_buffer
        self.extraction_passes = extraction_passes
        self.model = model  # tests inject a fake BaseLanguageModel

    def extract(self, text):
        kwargs = dict(
            text_or_documents=text,
            prompt_description=INSTRUCTION.format(max_claims=8),
            examples=_langextract_examples(self.lx),
            max_char_buffer=self.max_char_buffer,
            extraction_passes=self.extraction_passes,
            temperature=0.0,
            fetch_urls=False,
            show_progress=False,
            # Library defaults accept fuzzy and partial (MATCH_LESSER) alignments,
            # whose char_interval need not equal the extracted text. Disable both.
            resolver_params={"enable_fuzzy_alignment": False, "accept_match_lesser": False},
        )
        if self.model is not None:
            kwargs["model"] = self.model
        else:
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("LANGEXTRACT_API_KEY")
            if not key:
                raise ExtractorUnavailable("GEMINI_API_KEY_NOT_SET")
            kwargs.update(model_id=self.model_id, api_key=key)
        t0 = time.perf_counter()
        doc = self.lx.extract(**kwargs)
        latency = time.perf_counter() - t0
        cands = []
        for e in doc.extractions or []:
            attrs = e.attributes or {}
            ci = e.char_interval
            cands.append({
                "claim_text": attrs.get("claim_text") or e.extraction_text,
                "evidence_text": e.extraction_text,
                "char_start": ci.start_pos if ci else None,
                "char_end": ci.end_pos if ci else None,
                "modality": attrs.get("modality"),
                "asserted_by": attrs.get("asserted_by"),
                "event_date": attrs.get("event_date"),
                "extractor_alignment": e.alignment_status.value if e.alignment_status else "unaligned",
            })
        return {
            "candidates": cands,
            "meta": {
                "extractor": self.name, "model": self.model_id if self.model is None else "injected",
                "latency_s": round(latency, 3), "input_chars": len(text), "truncated": False,
                "prompt_tokens": None, "output_tokens": None,
                # LangExtract does not surface provider usage metadata in AnnotatedDocument.
                "token_usage_source": "NOT_MEASURED",
            },
        }
