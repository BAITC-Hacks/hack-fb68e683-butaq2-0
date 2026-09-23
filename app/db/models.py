"""SQLAlchemy models for the Voice Router configuration."""

from __future__ import annotations

from typing import Any

from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ScenarioRecord(Base):
    __tablename__ = "router_scenarios"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class SettingRecord(Base):
    __tablename__ = "router_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class AdminCredential(Base):
    __tablename__ = "router_admin_credentials"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdminChallenge(Base):
    __tablename__ = "router_admin_challenges"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdminSession(Base):
    __tablename__ = "router_admin_sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    credential_id: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
