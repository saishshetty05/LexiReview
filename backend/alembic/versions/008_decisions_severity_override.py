"""decisions.severity_override — CONTRACTS.md §7a (v1.8)

Revision ID: 008_decisions_severity_override
Revises: 007_documents_unique_constraint
Create Date: 2026-08-17

Severity personalization (internship-guide task): a reviewer can override a
finding's severity for themselves. Nullable column on the existing
`decisions` table rather than a new one -- an override is conceptually a
reviewer decision about a finding, same as accept/dismiss, and reuses the
same RLS/grant setup migration 006 already put in place. NULL means "no
override," not "override to nothing."
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "008_decisions_severity_override"
down_revision = "007_documents_unique_constraint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decisions", sa.Column("severity_override", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("decisions", "severity_override")
