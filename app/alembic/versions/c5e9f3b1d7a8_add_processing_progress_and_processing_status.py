from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'c5e9f3b1d7a8'
down_revision = 'a3f1c9d2e4b7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'casesheet_sessions',
        sa.Column('processing_progress', postgresql.JSON(astext_type=sa.Text()), nullable=True)
    )


def downgrade():
    op.drop_column('casesheet_sessions', 'processing_progress')
