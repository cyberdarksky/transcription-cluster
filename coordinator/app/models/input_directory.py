from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .base import Base


class InputDirectory(Base):
    """Watched input directory configuration."""

    __tablename__ = "input_directories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    # Absolute path on the coordinator filesystem.
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Corresponding output directory (absolute path).
    output_path: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    watch_recursively: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<InputDirectory path={self.path!r} active={self.is_active}>"
