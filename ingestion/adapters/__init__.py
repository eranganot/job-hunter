"""Source adapters: one class per source family, all behind a common interface."""
from .base import SourceAdapter, AdapterResult, AdapterStatus
from .bigtech import (
    MicrosoftAdapter, GoogleAdapter, MetaAdapter, AmazonAdapter, AppleAdapter,
)
from .aggregators import TheirStackAdapter, AdzunaAdapter, CoresignalAdapter
from .managed import ApifyAdapter, JobSpyAdapter
from .secretjobs import SecretJobsAdapter

__all__ = [
    "SourceAdapter", "AdapterResult", "AdapterStatus",
    "MicrosoftAdapter", "GoogleAdapter", "MetaAdapter", "AmazonAdapter", "AppleAdapter",
    "TheirStackAdapter", "AdzunaAdapter", "CoresignalAdapter",
    "ApifyAdapter", "JobSpyAdapter",
    "SecretJobsAdapter",
]
