from __future__ import annotations

from uuid import uuid4


def new_id(prefix: str) -> str:
    """Создать компактный публичный идентификатор с префиксом сущности."""
    safe_prefix = prefix.strip().lower().replace("-", "_")
    return f"{safe_prefix}_{uuid4().hex[:16]}"
