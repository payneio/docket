"""a move knows which run it was part of

Revision ID: f3b8c1d27a95
Revises: d4a1f6c92e07
Create Date: 2026-10-22 09:40:00.000000

Additive, and nullable: no move made before this knows its run, and none can be
told after the fact -- nothing recorded which moves were made together. The
board shows those as runs of one.
"""
import sqlalchemy as sa
from alembic import op

revision = 'f3b8c1d27a95'
down_revision = 'd4a1f6c92e07'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('moves', sa.Column('trace_id', sa.Text(), nullable=True), schema='docket')
    op.add_column('moves', sa.Column('span_id', sa.Text(), nullable=True), schema='docket')
    op.create_index(
        op.f('ix_docket_moves_trace_id'), 'moves', ['trace_id'], unique=False, schema='docket',
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_docket_moves_trace_id'), table_name='moves', schema='docket')
    op.drop_column('moves', 'span_id', schema='docket')
    op.drop_column('moves', 'trace_id', schema='docket')
