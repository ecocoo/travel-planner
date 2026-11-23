"""add pinned visits and modes"""

import sqlalchemy as sa
from alembic import op

revision = "5613cd756768"
down_revision = "901421e8d07e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trip_stops",
        sa.Column(
            "pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column("trip_stops", sa.Column("visit_mode", sa.String(length=20)))


def downgrade() -> None:
    op.drop_column("trip_stops", "visit_mode")
    op.drop_column("trip_stops", "pinned")
