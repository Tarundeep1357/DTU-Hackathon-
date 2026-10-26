"""Initial evidence review schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users", sa.Column("id", sa.String(80), primary_key=True), sa.Column("issuer", sa.String(500), nullable=False),
                    sa.Column("subject", sa.String(500), nullable=False), sa.Column("name", sa.String(200), nullable=False),
                    sa.Column("admin", sa.Boolean(), nullable=False), sa.UniqueConstraint("issuer", "subject"))
    op.create_table("cases", sa.Column("id", sa.String(80), primary_key=True), sa.Column("title", sa.String(200), nullable=False),
                    sa.Column("current_version", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_cases_expires_at", "cases", ["expires_at"])
    op.create_table("memberships", sa.Column("case_id", sa.String(80), sa.ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("user_id", sa.String(80), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True), sa.Column("role", sa.String(20), nullable=False))
    op.create_table("versions", sa.Column("case_id", sa.String(80), sa.ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("version", sa.Integer(), primary_key=True), sa.Column("payload", sa.JSON(), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("audit_events", sa.Column("id", sa.String(80), primary_key=True), sa.Column("case_id", sa.String(80)),
                    sa.Column("actor_id", sa.String(80), nullable=False), sa.Column("action", sa.String(100), nullable=False),
                    sa.Column("details", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_audit_events_case_id", "audit_events", ["case_id"])
    op.create_table("jobs", sa.Column("id", sa.String(80), primary_key=True), sa.Column("owner_key", sa.String(100), nullable=False),
                    sa.Column("kind", sa.String(30), nullable=False), sa.Column("state", sa.String(30), nullable=False),
                    sa.Column("payload", sa.JSON(), nullable=False), sa.Column("result", sa.JSON()), sa.Column("error", sa.Text()),
                    sa.Column("progress", sa.Integer(), nullable=False), sa.Column("message", sa.String(300), nullable=False),
                    sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("cancel_requested", sa.Boolean(), nullable=False),
                    sa.Column("lease_token", sa.String(80)), sa.Column("lease_until", sa.DateTime(timezone=True)),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("owner_key", "state", "expires_at"):
        op.create_index("ix_jobs_" + column, "jobs", [column])
    op.create_table("system_settings", sa.Column("key", sa.String(80), primary_key=True), sa.Column("value", sa.JSON(), nullable=False))


def downgrade():
    for table in ("system_settings", "jobs", "audit_events", "versions", "memberships", "cases", "users"):
        op.drop_table(table)

