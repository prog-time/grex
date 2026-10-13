from .product import (
    BatchItemResult,
    BatchParseRequest,
    BatchParseResponse,
    HealthResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobSummary,
    ParseRequest,
    ParseResponse,
    ProductCard,
)

# TortoiseORM-dependent models are imported lazily so that the core parsing
# logic works without tortoise-orm installed (e.g. in unit tests).
try:
    from .admin import AdminUser  # type: ignore[attr-defined]
    from .job import Job, JobStatus
except ImportError:
    AdminUser = None  # type: ignore[assignment,misc]
    Job = None  # type: ignore[assignment,misc]
    JobStatus = None  # type: ignore[assignment,misc]

__all__ = [
    "ProductCard",
    "ParseRequest",
    "ParseResponse",
    "HealthResponse",
    "BatchParseRequest",
    "BatchItemResult",
    "BatchParseResponse",
    "JobCreateRequest",
    "JobListResponse",
    "JobResponse",
    "JobSummary",
    "AdminUser",
    "Job",
    "JobStatus",
]
