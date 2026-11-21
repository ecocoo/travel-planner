"""create knowledge catalogue"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "dc496dc1ce26"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "cities",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "places",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("city_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("parent_place_id", sa.String(), nullable=True),
        sa.Column("record_status", sa.String(), nullable=False),
        sa.Column("route_ready", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="valid_latitude"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="valid_longitude"),
        sa.ForeignKeyConstraint(
            ["city_id"],
            ["cities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_place_id"], ["places.id"], initially="DEFERRED", deferrable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_places_city_id"), "places", ["city_id"], unique=False)
    op.create_table(
        "route_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("city_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("route_ready", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["city_id"],
            ["cities.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_route_templates_city_id"), "route_templates", ["city_id"], unique=False
    )
    op.create_table(
        "place_categories",
        sa.Column("place_id", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
        ),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("place_id", "category_id"),
    )
    op.create_index(
        op.f("ix_place_categories_category_id"),
        "place_categories",
        ["category_id"],
        unique=False,
    )
    op.create_table(
        "route_template_stops",
        sa.Column("route_id", sa.String(), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("place_id", sa.String(), nullable=False),
        sa.CheckConstraint(
            "day_number > 0 AND position > 0", name="positive_stop_position"
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
        ),
        sa.ForeignKeyConstraint(
            ["route_id"], ["route_templates.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("route_id", "day_number", "position"),
    )


def downgrade() -> None:
    op.drop_table("route_template_stops")
    op.drop_index(
        op.f("ix_place_categories_category_id"), table_name="place_categories"
    )
    op.drop_table("place_categories")
    op.drop_index(op.f("ix_route_templates_city_id"), table_name="route_templates")
    op.drop_table("route_templates")
    op.drop_index(op.f("ix_places_city_id"), table_name="places")
    op.drop_table("places")
    op.drop_table("cities")
    op.drop_table("categories")
