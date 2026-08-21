"""a secret that names a party

Revision ID: b3e91a7c5f28
Revises: a1c7f2b40d31
Create Date: 2026-08-21 13:45:00.000000

Additive. Nothing existing authenticates this way, and the owner's
`DOCKET_TOKEN` keeps working exactly as before -- it is the credential that
issues the others, so it cannot be one of them.
"""
import sqlalchemy as sa
from alembic import op

revision = 'b3e91a7c5f28'
down_revision = 'a1c7f2b40d31'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'credentials',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('party', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), nullable=True),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        schema='docket',
    )
    op.create_index(op.f('ix_docket_credentials_party'), 'credentials', ['party'],
                    unique=False, schema='docket')
    # Unique so identifying a caller is one index probe, not a scan.
    op.create_index(op.f('ix_docket_credentials_token_hash'), 'credentials', ['token_hash'],
                    unique=True, schema='docket')


def downgrade() -> None:
    op.drop_index(op.f('ix_docket_credentials_token_hash'), table_name='credentials',
                  schema='docket')
    op.drop_index(op.f('ix_docket_credentials_party'), table_name='credentials', schema='docket')
    op.drop_table('credentials', schema='docket')
