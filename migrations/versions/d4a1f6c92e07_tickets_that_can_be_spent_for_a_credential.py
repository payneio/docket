"""tickets that can be spent for a credential

Revision ID: d4a1f6c92e07
Revises: c7d2e8f31b94
Create Date: 2026-08-21 14:40:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'd4a1f6c92e07'
down_revision = 'c7d2e8f31b94'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'enrolments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('party', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), nullable=True),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('max_uses', sa.Integer(), server_default='1', nullable=False),
        sa.Column('used_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        schema='docket',
    )
    op.create_index(op.f('ix_docket_enrolments_party'), 'enrolments', ['party'],
                    unique=False, schema='docket')
    op.create_index(op.f('ix_docket_enrolments_token_hash'), 'enrolments', ['token_hash'],
                    unique=True, schema='docket')

    op.add_column('credentials', sa.Column('enrolment_id', sa.UUID(), nullable=True),
                  schema='docket')
    op.create_index(op.f('ix_docket_credentials_enrolment_id'), 'credentials', ['enrolment_id'],
                    unique=False, schema='docket')
    op.create_foreign_key(
        'fk_credentials_enrolment', 'credentials', 'enrolments', ['enrolment_id'], ['id'],
        source_schema='docket', referent_schema='docket', ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_credentials_enrolment', 'credentials', schema='docket',
                       type_='foreignkey')
    op.drop_index(op.f('ix_docket_credentials_enrolment_id'), table_name='credentials',
                  schema='docket')
    op.drop_column('credentials', 'enrolment_id', schema='docket')
    op.drop_index(op.f('ix_docket_enrolments_token_hash'), table_name='enrolments',
                  schema='docket')
    op.drop_index(op.f('ix_docket_enrolments_party'), table_name='enrolments', schema='docket')
    op.drop_table('enrolments', schema='docket')
