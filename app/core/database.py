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


async def migrate_user_hierarchy_schema() -> None:
    """Move tenant scope from organizations to a user hierarchy."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS parent_id BIGINT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE guidelines
                ADD COLUMN IF NOT EXISTS owner_user_id BIGINT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE guidelines
                ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE documents
                ADD COLUMN IF NOT EXISTS owner_user_id BIGINT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE documents
                ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE chunks
                ADD COLUMN IF NOT EXISTS owner_user_id BIGINT
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
                        FROM pg_constraint
                        WHERE conname = 'ck_users_role'
                          AND conrelid = 'users'::regclass
                    ) THEN
                        ALTER TABLE users DROP CONSTRAINT ck_users_role;
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE users
                SET role = CASE
                    WHEN lower(coalesce(role, '')) = 'admin' THEN 'admin'
                    WHEN lower(coalesce(role, '')) = 'hospital' THEN 'hospital'
                    WHEN lower(coalesce(role, '')) = 'doctor' THEN 'doctor'
                    WHEN lower(coalesce(role, '')) = 'health_department' THEN 'health_department'
                    ELSE 'health_department'
                END
                """
            )
        )
        await conn.execute(text("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'health_department'"))
        await conn.execute(text("ALTER TABLE users ALTER COLUMN role SET NOT NULL"))
        await conn.execute(
            text(
                """
                ALTER TABLE users
                ADD CONSTRAINT ck_users_role
                CHECK (role IN ('admin', 'health_department', 'hospital', 'doctor'))
                """
            )
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF to_regclass('public.organizations') IS NOT NULL
                       AND EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'users'
                              AND column_name = 'organization_id'
                       ) THEN
                        EXECUTE $sql$
                            INSERT INTO users (email, full_name, password_hash, role, is_active)
                            SELECT
                                'unit-' || o.slug || '@local.invalid',
                                o.name,
                                'disabled',
                                'health_department',
                                false
                            FROM organizations AS o
                            WHERE NOT EXISTS (
                                SELECT 1
                                FROM users AS u
                                WHERE u.organization_id = o.organization_id
                                  AND u.role <> 'admin'
                            )
                            ON CONFLICT (email) DO NOTHING
                        $sql$;
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO users (email, full_name, password_hash, role, is_active)
                SELECT
                    'default-health-department@local.invalid',
                    'Default Health Department',
                    'disabled',
                    'health_department',
                    false
                WHERE EXISTS (SELECT 1 FROM guidelines WHERE owner_user_id IS NULL)
                  AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'health_department')
                ON CONFLICT (email) DO NOTHING
                """
            )
        )
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF to_regclass('public.organizations') IS NOT NULL
                       AND EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'users'
                              AND column_name = 'organization_id'
                       )
                       AND EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'guidelines'
                              AND column_name = 'organization_id'
                       ) THEN
                        EXECUTE $sql$
                            WITH org_owner AS (
                                SELECT DISTINCT ON (u.organization_id)
                                    u.organization_id,
                                    u.user_id
                                FROM users AS u
                                WHERE u.organization_id IS NOT NULL
                                  AND u.role <> 'admin'
                                ORDER BY u.organization_id, u.is_active DESC, u.user_id ASC
                            )
                            UPDATE guidelines AS g
                            SET owner_user_id = org_owner.user_id
                            FROM org_owner
                            WHERE g.organization_id = org_owner.organization_id
                              AND g.owner_user_id IS NULL
                        $sql$;
                    END IF;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                WITH fallback_owner AS (
                    SELECT user_id
                    FROM users
                    WHERE role = 'health_department'
                    ORDER BY is_active DESC, user_id ASC
                    LIMIT 1
                )
                UPDATE guidelines
                SET owner_user_id = (SELECT user_id FROM fallback_owner)
                WHERE owner_user_id IS NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE guidelines
                SET created_by_user_id = owner_user_id
                WHERE created_by_user_id IS NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE documents AS d
                SET owner_user_id = g.owner_user_id,
                    created_by_user_id = COALESCE(d.created_by_user_id, g.created_by_user_id, g.owner_user_id)
                FROM guideline_versions AS v
                JOIN guidelines AS g ON g.guideline_id = v.guideline_id
                WHERE d.version_id = v.version_id
                  AND d.owner_user_id IS NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE chunks AS c
                SET owner_user_id = g.owner_user_id
                FROM guideline_versions AS v
                JOIN guidelines AS g ON g.guideline_id = v.guideline_id
                WHERE c.version_id = v.version_id
                  AND c.owner_user_id IS NULL
                """
            )
        )
        for table_name, column_name in (
            ("users", "parent_id"),
            ("users", "created_by_user_id"),
            ("guidelines", "owner_user_id"),
            ("guidelines", "created_by_user_id"),
            ("documents", "owner_user_id"),
            ("documents", "created_by_user_id"),
            ("chunks", "owner_user_id"),
        ):
            await conn.execute(
                text(
                    f"""
                    CREATE INDEX IF NOT EXISTS ix_{table_name}_{column_name}
                    ON {table_name} ({column_name})
                    """
                )
            )
        fk_specs = (
            ("users", "parent_id", "fk_users_parent_id", "users", "user_id", "RESTRICT"),
            ("users", "created_by_user_id", "fk_users_created_by_user_id", "users", "user_id", "SET NULL"),
            ("guidelines", "owner_user_id", "fk_guidelines_owner_user_id", "users", "user_id", "RESTRICT"),
            ("guidelines", "created_by_user_id", "fk_guidelines_created_by_user_id", "users", "user_id", "SET NULL"),
            ("documents", "owner_user_id", "fk_documents_owner_user_id", "users", "user_id", "RESTRICT"),
            ("documents", "created_by_user_id", "fk_documents_created_by_user_id", "users", "user_id", "SET NULL"),
            ("chunks", "owner_user_id", "fk_chunks_owner_user_id", "users", "user_id", "RESTRICT"),
        )
        for table_name, column_name, constraint_name, ref_table, ref_column, on_delete in fk_specs:
            await conn.execute(
                text(
                    f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_constraint
                            WHERE conname = '{constraint_name}'
                              AND conrelid = '{table_name}'::regclass
                        ) THEN
                            ALTER TABLE {table_name}
                            ADD CONSTRAINT {constraint_name}
                            FOREIGN KEY ({column_name})
                            REFERENCES {ref_table}({ref_column})
                            ON DELETE {on_delete};
                        END IF;
                    END $$;
                    """
                )
            )
        await conn.execute(text("ALTER TABLE guidelines ALTER COLUMN owner_user_id SET NOT NULL"))
        await conn.execute(text("ALTER TABLE documents ALTER COLUMN owner_user_id SET NOT NULL"))
        await conn.execute(text("ALTER TABLE chunks ALTER COLUMN owner_user_id SET NOT NULL"))
        for table_name in ("users", "guidelines", "documents", "chunks"):
            await conn.execute(text(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS fk_{table_name}_organization_id"))
            await conn.execute(text(f"DROP INDEX IF EXISTS ix_{table_name}_organization_id"))
            await conn.execute(text(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS organization_id"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations"))


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


async def migrate_documents_pipeline_mode_schema() -> None:
    """Add pipeline-mode metadata for uploaded documents."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE documents
                ADD COLUMN IF NOT EXISTS pipeline_mode_used VARCHAR(30)
                """
            )
        )


async def migrate_documents_original_filename_schema() -> None:
    """Preserve the user-uploaded PDF filename so the OCR pipeline can pass it verbatim to the partner core (matches local CLI behaviour)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                ALTER TABLE documents
                ADD COLUMN IF NOT EXISTS original_filename TEXT
                """
            )
        )
