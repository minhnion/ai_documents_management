from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.models import Base

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    echo=settings.DEBUG,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides an async database session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db_schema() -> None:
    """Create database tables if they do not exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def migrate_auth_schema_to_single_role() -> None:
    """
    Normalize auth schema to single-table RBAC on `users.role`.

    This keeps startup idempotent when code previously used roles/user_roles.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS role VARCHAR(20)
                NOT NULL DEFAULT 'viewer'
                """
            )
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'user_roles'
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'roles'
                    ) THEN
                        UPDATE users AS u
                        SET role = r.name
                        FROM user_roles AS ur
                        JOIN roles AS r ON r.role_id = ur.role_id
                        WHERE u.user_id = ur.user_id
                          AND r.name IN ('admin', 'editor', 'viewer');
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(text("DROP TABLE IF EXISTS user_roles"))
        await conn.execute(text("DROP TABLE IF EXISTS roles"))


async def migrate_sections_quality_schema() -> None:
    """Add minimal quality/page columns for section-level FE highlighting."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS page_start INTEGER
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS page_end INTEGER
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS start_y DOUBLE PRECISION
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS end_y DOUBLE PRECISION
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS match_score DOUBLE PRECISION
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS is_suspect BOOLEAN
                NOT NULL DEFAULT FALSE
                """
            )
        )


async def migrate_sections_enriched_schema() -> None:
    """Add richer OCR/TOC metadata columns for section-level grounding."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS node_id TEXT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS intro_content TEXT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS heading_bbox JSONB
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS content_bboxes JSONB
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE sections
                ADD COLUMN IF NOT EXISTS landing_chunks JSONB
                """
            )
        )


async def migrate_guidelines_ten_benh_schema() -> None:
    """Add optional disease-name column for guideline metadata."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE guidelines
                ADD COLUMN IF NOT EXISTS ten_benh TEXT
                """
            )
        )


async def migrate_chunks_text_abstract_schema() -> None:
    """Add LLM summary column for chunk retrieval payloads."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE chunks
                ADD COLUMN IF NOT EXISTS text_abstract TEXT
                """
            )
        )
