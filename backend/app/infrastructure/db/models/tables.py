from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
    text,
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


guest_sessions = Table(
    "guest_sessions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
)

trips = Table(
    "trips",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column(
        "owner_id",
        Uuid,
        ForeignKey("guest_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("city_id", String, ForeignKey("cities.id"), nullable=False),
    Column("title", String(200), nullable=False),
    Column("start_date", Date, nullable=False),
    Column("end_date", Date, nullable=False),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column(
        "updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    CheckConstraint(
        "end_date >= start_date AND end_date - start_date < 366",
        name="valid_trip_dates",
    ),
    CheckConstraint("revision > 0", name="positive_trip_revision"),
)

trip_stops = Table(
    "trip_stops",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("trip_id", Uuid, ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
    Column("day_number", Integer, nullable=False),
    Column("position", Integer, nullable=False),
    Column("place_id", String, ForeignKey("places.id"), nullable=False),
    Column("duration_minutes", Integer),
    Column("notes", String(2000), nullable=False, server_default=""),
    UniqueConstraint(
        "trip_id", "day_number", "position", name="unique_trip_stop_position"
    ),
    CheckConstraint(
        "day_number BETWEEN 1 AND 366 AND position > 0", name="valid_trip_stop_position"
    ),
    CheckConstraint(
        "duration_minutes BETWEEN 1 AND 1440", name="valid_trip_stop_duration"
    ),
)
