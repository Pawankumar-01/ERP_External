from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '21685b7714f1'
down_revision = 'db6e388cbd64'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('leads')


def downgrade():
    op.create_table('leads',
    sa.Column('id', sa.VARCHAR(length=36), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=200), autoincrement=False, nullable=False),
    sa.Column('phone', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('email', sa.VARCHAR(length=200), autoincrement=False, nullable=True),
    sa.Column('lead_source', postgresql.ENUM('WEBSITE', 'REFERRAL', 'SOCIAL_MEDIA', 'WALK_IN', 'PHONE', 'OTHER', name='leadsource'), autoincrement=False, nullable=False),
    sa.Column('interested_in', postgresql.ENUM('CONSULTATION', 'DEVICE', 'BOTH', name='interestedin'), autoincrement=False, nullable=False),
    sa.Column('status', postgresql.ENUM('NEW', 'ORIENTATION_SCHEDULED', 'ORIENTATION_ATTENDED', 'APPOINTMENT_SCHEDULED', 'CONVERTED', 'DORMANT', name='leadstatus'), autoincrement=False, nullable=False),
    sa.Column('notes', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('erp_patient_id', sa.VARCHAR(length=100), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('leads_pkey'))
    )
