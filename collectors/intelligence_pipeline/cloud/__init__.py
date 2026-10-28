"""Opt-in cloud adapters for the existing EXP-009 Worker; imports perform no IO."""

CONTRACT = "exp009-unified-auto09-v2"
MODEL = "gemini-3.5-flash-lite"
MAX_INPUT_TOKENS = 1_048_576
MAX_OUTPUT_TOKENS = 6000
TRIAL_MICRO_USD = 1_000_000


def cost(usage):
    """Standard text price, rounded UP in micro-USD, including thought output."""
    return (usage.input_tokens * 3 + usage.output_tokens * 25 + 9) // 10
