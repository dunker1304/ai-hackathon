"""Crawl sessions and raw crawl output

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-21
"""

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("marketplace", sa.Text(), nullable=False, server_default="amazon"),
        # request
        sa.Column("keywords", JSONB(), nullable=False, server_default="[]"),
        sa.Column("region", sa.Text(), nullable=False, server_default="us"),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("max_products", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("options", JSONB(), nullable=False, server_default="{}"),
        # execution
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("phase_detail", sa.Text(), nullable=True),
        sa.Column("links_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_total", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("quality", JSONB(), nullable=False, server_default="{}"),
        sa.Column("warnings", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_crawl_sessions_status", "crawl_sessions", ["status"])
    # The status page lists recent sessions newest-first.
    op.create_index("ix_crawl_sessions_created_at", "crawl_sessions", [sa.text("created_at DESC")])

    op.create_table(
        "crawl_keywords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("crawl_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("links_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stopped_reason", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_crawl_keywords_session_id", "crawl_keywords", ["session_id"])

    op.create_table(
        "crawl_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Text(),
            sa.ForeignKey("crawl_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("marketplace", sa.Text(), nullable=False, server_default="amazon"),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        # discovery
        sa.Column("keyword", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("sponsored", sa.Boolean(), nullable=False, server_default=sa.false()),
        # detail
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("brand", sa.Text(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("list_price", sa.Float(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("bought_past_month", sa.Integer(), nullable=True),
        sa.Column("availability", sa.Text(), nullable=True),
        sa.Column("unshippable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("best_seller_ranks", JSONB(), nullable=False, server_default="[]"),
        sa.Column("categories", JSONB(), nullable=False, server_default="[]"),
        sa.Column("bullets", JSONB(), nullable=False, server_default="[]"),
        sa.Column("attributes", JSONB(), nullable=False, server_default="{}"),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("parent_asin", sa.Text(), nullable=True),
        sa.Column("variation_count", sa.Integer(), nullable=True),
        sa.Column("parse_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("detail_error", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_id", "external_id", name="uq_crawl_products_session_external"),
    )
    op.create_index("ix_crawl_products_session_id", "crawl_products", ["session_id"])
    op.create_index("ix_crawl_products_external_id", "crawl_products", ["external_id"])
    op.create_index("ix_crawl_products_keyword", "crawl_products", ["keyword"])
    # Cross-session history for one product ("has this ASIN been crawled before?").
    op.create_index(
        "ix_crawl_products_marketplace_external",
        "crawl_products",
        ["marketplace", "external_id"],
    )


def downgrade() -> None:
    op.drop_table("crawl_products")
    op.drop_table("crawl_keywords")
    op.drop_table("crawl_sessions")
