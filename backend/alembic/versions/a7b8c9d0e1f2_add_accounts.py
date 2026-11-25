"""Add registered accounts while preserving anonymous owners."""

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "4a6f9d10b2c3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100)),
        sa.Column("email", sa.String(254), unique=True),
        sa.Column("password_hash", sa.String()),
        sa.Column("role", sa.String(20), nullable=False, server_default="traveler"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("role IN ('traveler', 'guide')", name="valid_user_role"),
        sa.CheckConstraint(
            "(email IS NULL AND name IS NULL AND password_hash IS NULL "
            "AND role = 'traveler') "
            "OR (email IS NOT NULL AND name IS NOT NULL AND password_hash IS NOT NULL)",
            name="complete_user_account",
        ),
    )
    op.execute(
        "INSERT INTO users (id, created_at) SELECT id, created_at FROM guest_sessions"
    )
    op.create_foreign_key(
        "guest_sessions_id_fkey",
        "guest_sessions",
        "users",
        ["id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column("guest_sessions", sa.Column("claimed_by", sa.Uuid()))
    op.create_foreign_key(
        "guest_sessions_claimed_by_fkey",
        "guest_sessions",
        "users",
        ["claimed_by"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("trips_owner_id_fkey", "trips", type_="foreignkey")
    op.create_foreign_key(
        "trips_owner_id_fkey",
        "trips",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])


def downgrade():
    # Preserve account-owned trips under anonymous owners for the older schema.
    op.execute("""
        INSERT INTO guest_sessions (id, token_hash, created_at)
        SELECT u.id, md5(gen_random_uuid()::text) || md5(gen_random_uuid()::text),
               u.created_at FROM users u
        WHERE NOT EXISTS (SELECT 1 FROM guest_sessions g WHERE g.id = u.id)
    """)
    op.drop_constraint("trips_owner_id_fkey", "trips", type_="foreignkey")
    op.create_foreign_key(
        "trips_owner_id_fkey",
        "trips",
        "guest_sessions",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("guest_sessions", "claimed_by")
    op.drop_constraint("guest_sessions_id_fkey", "guest_sessions", type_="foreignkey")
    op.drop_table("auth_sessions")
    op.drop_table("users")
