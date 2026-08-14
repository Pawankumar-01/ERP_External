"""Add patient_name, last_error columns and doctor_status index to casesheet_sessions

Revision ID: a3f1c9d2e4b7
Revises: 21685b7714f1
Create Date: 2026-08-14 11:40:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a3f1c9d2e4b7'
down_revision = '21685b7714f1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('casesheet_sessions', sa.Column('patient_name', sa.String(200), nullable=True))
    op.add_column('casesheet_sessions', sa.Column('last_error', sa.Text(), nullable=True))
    op.create_index('ix_session_doctor_status', 'casesheet_sessions', ['doctor_id', 'status'])


def downgrade():
    op.drop_index('ix_session_doctor_status', table_name='casesheet_sessions')
    op.drop_column('casesheet_sessions', 'last_error')
    op.drop_column('casesheet_sessions', 'patient_name')
