"""The names of every state, in one place."""

STANDARD = "standard"
PRIVATE = "private"

# A document
QUEUED = "queued"
INDEXING = "indexing"
INDEXED = "indexed"
FAILED = "failed"
CANCELLED = "cancelled"
MISSING = "missing"
REMOVED = "removed"
NOT_DOWNLOADED = "not_downloaded"
SKIPPED_PASSWORD = "skipped_password"  # noqa: S105
SKIPPED_UNSUPPORTED = "skipped_unsupported"

# A job (also QUEUED, FAILED, CANCELLED)
RUNNING = "running"
SUCCEEDED = "succeeded"
SKIPPED = "skipped"

# A revision
STAGING = "staging"
ACTIVE = "active"
RETIRED = "retired"

# A watched folder
SCANNING = "scanning"
WATCHING = "watching"
NEEDS_ATTENTION = "needs_attention"
PAUSED = "paused"

# A card (also FAILED)
PENDING = "pending"
COMPLETE = "complete"

# A chart or figure (also PENDING, QUEUED, FAILED)
READING = "reading"
READ = "read"

# Storage of a location
REFERENCED = "referenced"
MANAGED = "managed"

LIBRARY_SOURCE_ID = "library"
