"""tasks, documents, inbound email

Adds the three tables that back the operator work queue and the mailbox /
document vaults. They were declared as models but never migrated:

* ``tasks`` — human work items (``app.models.task``).
* ``documents`` — uploaded files plus extracted text and structured payload
  (``app.models.document``).
* ``inbound_emails`` — one row per RFC5322 Message-ID pulled from the mailbox
  (``app.models.email``).

Every enum is stored as a ``VARCHAR`` (``native_enum=False``) so the schema
behaves identically on Postgres and SQLite; the ``length=`` values mirror the
models exactly.

Revision ID: c3a7e1d94b62
Revises: b2f1c9a4d7e0
Create Date: 2026-10-04 23:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3a7e1d94b62"
down_revision: str | None = "b2f1c9a4d7e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "TODO",
                "IN_PROGRESS",
                "BLOCKED",
                "DONE",
                "CANCELLED",
                name="taskstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="2"),
        sa.Column(
            "task_type",
            sa.Enum(
                "CUSTOMER_FOLLOWUP",
                "BOOKING_ISSUE",
                "REFUND",
                "DOCUMENT_REVIEW",
                "LOGISTICS",
                "REPORT",
                "OTHER",
                name="tasktype",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("assignee_id", sa.String(length=36), nullable=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("booking_id", sa.String(length=36), nullable=True),
        sa.Column("escalation_id", sa.String(length=36), nullable=True),
        sa.Column("document_id", sa.String(length=120), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
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
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["escalation_id"], ["escalations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_id"), "tasks", ["id"], unique=False)
    op.create_index(op.f("ix_tasks_assignee_id"), "tasks", ["assignee_id"], unique=False)
    op.create_index("ix_tasks_status_due", "tasks", ["status", "due_at"], unique=False)

    op.create_table(
        "inbound_emails",
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("from_address", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("to_address", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("subject", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column(
            "intent",
            sa.Enum(
                "FLIGHT_BOOKING",
                "BOOKING_CHANGE",
                "REFUND_REQUEST",
                "INVOICE_OR_DOCUMENT",
                "COMPLAINT",
                "GENERAL_INQUIRY",
                "SPAM",
                "UNKNOWN",
                name="emailintent",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                "PROCESSING",
                "PROCESSED",
                "FAILED",
                "IGNORED",
                name="inboundemailstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_meta", sa.JSON(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
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
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_inbound_emails_id"), "inbound_emails", ["id"], unique=False)
    op.create_index(
        op.f("ix_inbound_emails_message_id"), "inbound_emails", ["message_id"], unique=True
    )
    op.create_index(
        op.f("ix_inbound_emails_conversation_id"),
        "inbound_emails",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(op.f("ix_inbound_emails_intent"), "inbound_emails", ["intent"], unique=False)
    op.create_index(op.f("ix_inbound_emails_status"), "inbound_emails", ["status"], unique=False)
    op.create_index(
        "ix_inbound_emails_status_created",
        "inbound_emails",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_inbound_emails_intent_created",
        "inbound_emails",
        ["intent", "created_at"],
        unique=False,
    )

    op.create_table(
        "documents",
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "UPLOADED",
                "EXTRACTING",
                "EXTRACTED",
                "FAILED",
                name="documentstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "doc_type",
            sa.Enum(
                "AIRLINE_TICKET",
                "INVOICE",
                "ITINERARY",
                "PASSPORT",
                "CONTRACT",
                "PURCHASE_ORDER",
                "OTHER",
                name="documenttype",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("extracted", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("needs_ocr", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("uploaded_by_id", sa.String(length=36), nullable=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
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
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_documents_id"), "documents", ["id"], unique=False)
    op.create_index(op.f("ix_documents_sha256"), "documents", ["sha256"], unique=False)
    op.create_index(
        op.f("ix_documents_conversation_id"), "documents", ["conversation_id"], unique=False
    )
    op.create_index(
        op.f("ix_documents_uploaded_by_id"), "documents", ["uploaded_by_id"], unique=False
    )
    op.create_index(
        "ix_documents_status_created",
        "documents",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop in reverse dependency order: nothing references these three tables."""
    op.drop_index("ix_documents_status_created", table_name="documents")
    op.drop_index(op.f("ix_documents_uploaded_by_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_conversation_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_sha256"), table_name="documents")
    op.drop_index(op.f("ix_documents_id"), table_name="documents")
    op.drop_table("documents")

    op.drop_index("ix_inbound_emails_intent_created", table_name="inbound_emails")
    op.drop_index("ix_inbound_emails_status_created", table_name="inbound_emails")
    op.drop_index(op.f("ix_inbound_emails_status"), table_name="inbound_emails")
    op.drop_index(op.f("ix_inbound_emails_intent"), table_name="inbound_emails")
    op.drop_index(op.f("ix_inbound_emails_conversation_id"), table_name="inbound_emails")
    op.drop_index(op.f("ix_inbound_emails_message_id"), table_name="inbound_emails")
    op.drop_index(op.f("ix_inbound_emails_id"), table_name="inbound_emails")
    op.drop_table("inbound_emails")

    op.drop_index("ix_tasks_status_due", table_name="tasks")
    op.drop_index(op.f("ix_tasks_assignee_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_id"), table_name="tasks")
    op.drop_table("tasks")
