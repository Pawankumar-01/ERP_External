"""Add processing_progress column to casesheet_sessions

Revision ID: c5e9f3b1d7a8
Revises: a3f1c9d2e4b7
Create Date: 2026-08-21 18:55:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c5e9f3b1d7a8'
down_revision = 'a3f1c9d2e4b7'
branch_labels = None
depends_on = None


def upgrade():
    # Add processing_progress JSON column to casesheet_sessions table
    op.add_column(
        'casesheet_sessions',
        sa.Column('processing_progress', postgresql.JSON(astext_type=sa.Text()), nullable=True)
    )


def downgrade():
    op.drop_column('casesheet_sessions', 'processing_progress')
