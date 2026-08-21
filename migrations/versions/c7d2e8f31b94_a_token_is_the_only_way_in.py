"""a token is the only way in

Revision ID: c7d2e8f31b94
Revises: b3e91a7c5f28
Create Date: 2026-08-21 14:20:00.000000

Peer credentials go, and the two columns that existed for them.

`local_identities` mapped a unix uid onto a party, with the kernel vouching over
a socket and no credential presented at all. It was the better answer where it
applied and it applied to three processes on one machine; credentials do
everything it did from anywhere.

`moves.attested` recorded whether the kernel had vouched. **This drops data**:
ten moves in the live register are `attested = true` and that fact does not
survive. It is dropped rather than kept because nothing ever branched on it and
nothing could ever set it again -- a column that can only be false is worse than
no column. `moves.via_credential` replaces it with the half that does work:
which credential made this, so revoking one answers what it touched.
"""
import sqlalchemy as sa
from alembic import op

revision = 'c7d2e8f31b94'
down_revision = 'b3e91a7c5f28'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('moves', sa.Column('via_credential', sa.UUID(), nullable=True), schema='docket')
    op.create_index(op.f('ix_docket_moves_via_credential'), 'moves', ['via_credential'],
                    unique=False, schema='docket')
    op.create_foreign_key(
        'fk_moves_via_credential', 'moves', 'credentials', ['via_credential'], ['id'],
        source_schema='docket', referent_schema='docket', ondelete='SET NULL',
    )
    op.drop_column('moves', 'attested', schema='docket')
    op.drop_table('local_identities', schema='docket')


def downgrade() -> None:
    op.create_table(
        'local_identities',
        sa.Column('uid', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('party', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), nullable=True),
        sa.Column('registered_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('uid'),
        schema='docket',
    )
    op.create_index(op.f('ix_docket_local_identities_party'), 'local_identities', ['party'],
                    unique=False, schema='docket')
    # The uids themselves are gone; this restores the shape, never the rows.
    op.add_column('moves', sa.Column('attested', sa.Boolean(), server_default='false',
                                     nullable=False), schema='docket')
    op.drop_constraint('fk_moves_via_credential', 'moves', schema='docket', type_='foreignkey')
    op.drop_index(op.f('ix_docket_moves_via_credential'), table_name='moves', schema='docket')
    op.drop_column('moves', 'via_credential', schema='docket')
