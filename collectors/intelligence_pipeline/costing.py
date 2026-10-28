"""Versioned, integer Standard text pricing; never an authorization to spend."""
from .contracts import Usage

MODEL = "gemini-3.5-flash-lite"
PRICE_VERSION = "gemini35fl-standard-input26-v1"
MAX_INPUT_TOKENS = 1_048_576
MAX_OUTPUT_TOKENS = 6000
MAX_TRIAL_MICRO_USD = 1_000_000


def micro_usd(usage):
    """$0.30/M input, $2.50/M output including thoughts, rounded up per call."""
    if not isinstance(usage, Usage):
        raise ValueError("TYPED_USAGE_REQUIRED")
    return (usage.input_tokens * 3 + usage.output_tokens * 25 + 9) // 10


def paid_cap(value):
    if type(value) is not int or not 1 <= value <= MAX_TRIAL_MICRO_USD:
        raise ValueError("EXPLICIT_TOTAL_CAP_UP_TO_ONE_DOLLAR_REQUIRED")
    return value
