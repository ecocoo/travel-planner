"""add guest trips and itinerary stops"""

from alembic import op
import sqlalchemy as sa

revision = "901421e8d07e"
down_revision = "dc496dc1ce26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guest_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_table(
        "trips",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("city_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "revision", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "end_date >= start_date AND end_date - start_date < 366",
            name="valid_trip_dates",
        ),
        sa.CheckConstraint("revision > 0", name="positive_trip_revision"),
        sa.ForeignKeyConstraint(
            ["city_id"],
            ["cities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["guest_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trips_owner_id"), "trips", ["owner_id"], unique=False)
    op.create_table(
        "trip_stops",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("place_id", sa.String(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(length=2000), server_default="", nullable=False),
        sa.CheckConstraint(
            "day_number BETWEEN 1 AND 366 AND position > 0",
            name="valid_trip_stop_position",
        ),
        sa.CheckConstraint(
            "duration_minutes BETWEEN 1 AND 1440", name="valid_trip_stop_duration"
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
        ),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trip_id", "day_number", "position", name="unique_trip_stop_position"
        ),
    )


def downgrade() -> None:
    op.drop_table("trip_stops")
    op.drop_index(op.f("ix_trips_owner_id"), table_name="trips")
    op.drop_table("trips")
    op.drop_table("guest_sessions")
