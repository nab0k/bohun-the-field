"""Data passed between source adapters and the pipeline (EXP-012)."""
from dataclasses import dataclass, field


@dataclass
class SignalIn:
    type: str                      # see taxonomy.SIGNAL_LAYER / schema CHECK
    counterpart: str = None        # buyer, prime contractor or programme
    date: str = None               # ISO date
    url: str = None
    layer: str = None              # explicit layer (legacy assessment); otherwise from type


@dataclass
class Mention:
    """One company as seen in one source record."""
    name: str
    country: str = None            # ISO2; legal or as reported by the source
    city: str = None               # dropped for UA by the store
    website: str = None
    identifiers: list = field(default_factory=list)   # [(scheme, value)]
    codes: list = field(default_factory=list)         # [(scheme, code)] -> classification
    signals: list = field(default_factory=list)       # [SignalIn]
    supplies_to: list = field(default_factory=list)   # [(scheme, value)] of the buyer company (subcontracts)
    parents: list = field(default_factory=list)       # [(scheme, value)] of the parent company (corporate group)
    notion_page_id: str = None
    flags: list = field(default_factory=list)
    legacy_categories: list = field(default_factory=list)  # [(axis, value, basis)]
    supply_role: str = None


@dataclass
class Discovery:
    """Source/event lead; NOT a verified legal entity."""
    name: str
    reported_country: str = None
    website: str = None
    role: str = "lead"


@dataclass
class Record:
    """One raw record from a source (stored as observation)."""
    source: str
    source_key: str
    payload: dict
    mentions: list = field(default_factory=list)
    discoveries: list = field(default_factory=list)
