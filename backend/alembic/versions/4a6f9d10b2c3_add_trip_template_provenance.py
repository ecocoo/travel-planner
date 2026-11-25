"""Store a snapshot of the source route template on copied trips."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "4a6f9d10b2c3"
down_revision = "5613cd756768"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trips", sa.Column("source_template", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("trips", "source_template")
