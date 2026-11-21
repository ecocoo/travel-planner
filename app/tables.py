from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

cities = Table(
    "cities",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("timezone", String, nullable=False),
    Column("payload", JSONB, nullable=False),
)

categories = Table(
    "categories",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
)

places = Table(
    "places",
    metadata,
    Column("id", String, primary_key=True),
    Column("city_id", String, ForeignKey("cities.id"), nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("entity_type", String, nullable=False),
    Column("latitude", Float),
    Column("longitude", Float),
    Column(
        "parent_place_id",
        String,
        ForeignKey("places.id", deferrable=True, initially="DEFERRED"),
    ),
    Column("record_status", String, nullable=False),
    Column("route_ready", Boolean, nullable=False),
    Column("payload", JSONB, nullable=False),
    CheckConstraint("latitude BETWEEN -90 AND 90", name="valid_latitude"),
    CheckConstraint("longitude BETWEEN -180 AND 180", name="valid_longitude"),
)

place_categories = Table(
    "place_categories",
    metadata,
    Column(
        "place_id",
        String,
        ForeignKey("places.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "category_id", String, ForeignKey("categories.id"), primary_key=True, index=True
    ),
)

route_templates = Table(
    "route_templates",
    metadata,
    Column("id", String, primary_key=True),
    Column("city_id", String, ForeignKey("cities.id"), nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("route_ready", Boolean, nullable=False),
    Column("payload", JSONB, nullable=False),
)

route_template_stops = Table(
    "route_template_stops",
    metadata,
    Column(
        "route_id",
        String,
        ForeignKey("route_templates.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("day_number", Integer, primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("place_id", String, ForeignKey("places.id"), nullable=False),
    CheckConstraint("day_number > 0 AND position > 0", name="positive_stop_position"),
)
