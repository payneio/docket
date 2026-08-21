"""an entry may be waiting on another

Revision ID: a1c7f2b40d31
Revises: 23eda78dba0e
Create Date: 2026-08-21 11:40:00.000000

Additive, and nullable: every existing entry is simply not blocked. The one
conversation in the live register that *was* blocked recorded it as a sentence
in an `update` note, which no migration can promote into an edge -- the note
stays, and the edge starts being kept from here.
"""
import sqlalchemy as sa
from alembic import op

revision = 'a1c7f2b40d31'
down_revision = '23eda78dba0e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('entries', sa.Column('blocked_on', sa.UUID(), nullable=True), schema='docket')
    op.create_index(
        op.f('ix_docket_entries_blocked_on'), 'entries', ['blocked_on'],
        unique=False, schema='docket',
    )
    op.create_foreign_key(
        'fk_entries_blocked_on_entries', 'entries', 'entries',
        ['blocked_on'], ['id'], source_schema='docket', referent_schema='docket',
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_entries_blocked_on_entries', 'entries', schema='docket', type_='foreignkey')
    op.drop_index(op.f('ix_docket_entries_blocked_on'), table_name='entries', schema='docket')
    op.drop_column('entries', 'blocked_on', schema='docket')
