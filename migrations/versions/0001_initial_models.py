"""initial models

Revision ID: 0001
Revises:
Create Date: 2026-11-03 00:51:23.393336
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "executors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("specialization", sa.String(length=255), nullable=True),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_executors_name_nonempty"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "resident",
                "dispatcher",
                name="user_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("length(trim(login)) > 0", name="ck_users_login_nonempty"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("login"),
    )
    op.create_table(
        "service_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("resident_id", sa.Integer(), nullable=False),
        sa.Column("executor_id", sa.Integer(), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "new",
                "in_progress",
                "closed",
                name="request_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("closing_comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(description)) > 0",
            name="ck_service_requests_description_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(location)) > 0", name="ck_service_requests_location_nonempty"
        ),
        sa.ForeignKeyConstraint(["executor_id"], ["executors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resident_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_service_requests_executor_id"),
        "service_requests",
        ["executor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_service_requests_resident_id"),
        "service_requests",
        ["resident_id"],
        unique=False,
    )
    op.create_table(
        "request_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column(
            "previous_status",
            sa.Enum(
                "draft",
                "new",
                "in_progress",
                "closed",
                name="history_previous_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "new_status",
            sa.Enum(
                "draft",
                "new",
                "in_progress",
                "closed",
                name="history_new_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["request_id"], ["service_requests.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_request_history_actor_id"),
        "request_history",
        ["actor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_request_history_request_id"),
        "request_history",
        ["request_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_request_history_request_id"), table_name="request_history")
    op.drop_index(op.f("ix_request_history_actor_id"), table_name="request_history")
    op.drop_table("request_history")
    op.drop_index(
        op.f("ix_service_requests_resident_id"), table_name="service_requests"
    )
    op.drop_index(
        op.f("ix_service_requests_executor_id"), table_name="service_requests"
    )
    op.drop_table("service_requests")
    op.drop_table("users")
    op.drop_table("executors")
