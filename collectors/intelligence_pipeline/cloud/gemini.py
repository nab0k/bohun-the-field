"""Compatibility import of the single FREE_ONLY model adapter. No paid path."""
from ..gemini import GeminiModel, FreeApproval, ModelAccessStop, build_request
from . import MAX_OUTPUT_TOKENS


def request_for(stage, payload):
    return build_request(stage, payload, MAX_OUTPUT_TOKENS)
