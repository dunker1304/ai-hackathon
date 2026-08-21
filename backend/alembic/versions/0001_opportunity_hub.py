"""Product Opportunity Hub tables

Revision ID: 0001
Revises:
Create Date: 2026-08-20
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1536  # must match app/models.py


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "taxonomy",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("material", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("margin_min", sa.Float(), nullable=False),
        sa.Column("margin_max", sa.Float(), nullable=False),
        sa.Column(
            "personalization_friendly",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("fit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("seasonality", JSONB(), nullable=False, server_default="{}"),
    )

    op.create_table(
        "taxonomy_aliases",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "product_type_id",
            sa.Text(),
            sa.ForeignKey("taxonomy.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    )
    op.execute(
        "CREATE INDEX taxonomy_aliases_embedding_hnsw_idx "
        "ON taxonomy_aliases USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    op.create_table(
        "listings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("favorites", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("est_sales", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shop", sa.Text(), nullable=False),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "product_type_id", sa.Text(), sa.ForeignKey("taxonomy.id"), nullable=True
        ),
        sa.Column("norm_confidence", sa.Float(), nullable=True),
    )
    op.create_index("listings_product_type_idx", "listings", ["product_type_id"])

    op.create_table(
        "keywords",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("competition", sa.Float(), nullable=False),
        sa.Column("cpc", sa.Float(), nullable=False),
        sa.Column("trend_30d", sa.Float(), nullable=False),
        sa.Column(
            "product_type_id", sa.Text(), sa.ForeignKey("taxonomy.id"), nullable=True
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "trends",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
    )
    op.create_index("trends_entity_date_idx", "trends", ["entity", "date"])

    op.create_table(
        "scores",
        sa.Column(
            "product_type_id",
            sa.Text(),
            sa.ForeignKey("taxonomy.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("dims", JSONB(), nullable=False),
        sa.Column("total", sa.Float(), nullable=False),
        sa.Column("fit", sa.Integer(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("scores")
    op.drop_table("trends")
    op.drop_table("keywords")
    op.drop_table("listings")
    op.drop_table("taxonomy_aliases")
    op.drop_table("taxonomy")
