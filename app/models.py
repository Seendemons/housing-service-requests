"""Схема данных заявок; бизнес-операции здесь не реализованы."""

from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Role(str, Enum):
    RESIDENT = "resident"
    DISPATCHER = "dispatcher"


class RequestStatus(str, Enum):
    DRAFT = "draft"
    NEW = "new"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


def enum_type(enum: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda cls: [item.value for item in cls],
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("length(trim(login)) > 0", name="ck_users_login_nonempty"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(enum_type(Role, "user_role"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    requests: Mapped[list["ServiceRequest"]] = relationship(
        back_populates="resident", passive_deletes="all"
    )
    history_entries: Mapped[list["RequestHistory"]] = relationship(
        back_populates="actor", passive_deletes="all"
    )


class Executor(Base):
    __tablename__ = "executors"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_executors_name_nonempty"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    specialization: Mapped[str | None] = mapped_column(String(255))
    requests: Mapped[list["ServiceRequest"]] = relationship(
        back_populates="executor", passive_deletes="all"
    )


class ServiceRequest(Base):
    __tablename__ = "service_requests"
    __table_args__ = (
        CheckConstraint(
            "length(trim(location)) > 0", name="ck_service_requests_location_nonempty"
        ),
        CheckConstraint(
            "length(trim(description)) > 0",
            name="ck_service_requests_description_nonempty",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    resident_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    executor_id: Mapped[int | None] = mapped_column(
        ForeignKey("executors.id", ondelete="RESTRICT"), index=True
    )
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        enum_type(RequestStatus, "request_status"),
        default=RequestStatus.DRAFT,
        server_default="draft",
        nullable=False,
    )
    closing_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        onupdate=utc_now,
        nullable=False,
    )
    resident: Mapped[User] = relationship(back_populates="requests")
    executor: Mapped[Executor | None] = relationship(back_populates="requests")
    history: Mapped[list["RequestHistory"]] = relationship(
        back_populates="request", passive_deletes="all"
    )


class RequestHistory(Base):
    __tablename__ = "request_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("service_requests.id", ondelete="RESTRICT"), index=True
    )
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    previous_status: Mapped[RequestStatus | None] = mapped_column(
        enum_type(RequestStatus, "history_previous_status")
    )
    new_status: Mapped[RequestStatus] = mapped_column(
        enum_type(RequestStatus, "history_new_status"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    request: Mapped[ServiceRequest] = relationship(back_populates="history")
    actor: Mapped[User] = relationship(back_populates="history_entries")
