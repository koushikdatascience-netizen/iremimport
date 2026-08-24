"""Phase 2 import processor placeholder.

Madhushala API calls, item matching, mapping, and CRM integration are
intentionally out of scope for Phase 1.
"""


class ImportProcessor:
    @classmethod
    async def start(cls) -> None:
        return None

    @classmethod
    async def stop(cls) -> None:
        return None

    @classmethod
    async def queue_batch(cls, items: dict) -> str:
        raise NotImplementedError("Import processing is not implemented in Phase 1")
