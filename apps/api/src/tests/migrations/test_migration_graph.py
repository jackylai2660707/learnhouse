import ast
import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


API_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = API_ROOT / "migrations" / "versions"
PILOT_REVISION = "cd4e5f6a7b8c"
PERFORMANCE_REVISION = "d5e6f7a8b9c0"
ESSAY_REVISION = "e6f7a8b9c0d1"
RAG_REVISION = "g8h9i0j1k2l3"
CURRENT_HEAD = "i0j1k2l3m4n5"
ASSIGNMENT_JSON_HARDENING_REVISION = "i0j1k2l3m4n5"


def _revision_id(path: Path) -> str:
    module = ast.parse(path.read_text())
    for node in module.body:
        target_name = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    target_name = target.id
                    value = node.value
                    break
        if (
            target_name == "revision"
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            return value.value
    raise AssertionError(f"{path.name} does not define a string revision id")


def _script_directory() -> ScriptDirectory:
    config = Config(str(API_ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(config)


def test_alembic_revision_ids_are_unique():
    revisions_by_id: dict[str, list[str]] = {}
    for path in MIGRATIONS_DIR.glob("*.py"):
        revisions_by_id.setdefault(_revision_id(path), []).append(path.name)

    duplicates = {
        revision: names for revision, names in revisions_by_id.items() if len(names) > 1
    }

    assert duplicates == {}


def test_alembic_has_single_head_with_school_pilot_migration_in_ancestry():
    script = _script_directory()
    heads = list(script.get_heads())

    assert heads == [CURRENT_HEAD]

    head = heads[0]
    ancestry = {
        revision.revision for revision in script.iterate_revisions(head, "base")
    }

    assert {
        PILOT_REVISION,
        PERFORMANCE_REVISION,
        ESSAY_REVISION,
        RAG_REVISION,
        ASSIGNMENT_JSON_HARDENING_REVISION,
    }.issubset(ancestry)


def test_performance_indexes_migration_is_safe_for_existing_school_installations():
    migration_text = (
        MIGRATIONS_DIR / "d5e6f7a8b9c0_add_missing_performance_indexes.py"
    ).read_text()

    assert 'down_revision: Union[str, None] = "cd4e5f6a7b8c"' in migration_text
    assert (
        "if not table_columns or not set(columns).issubset(table_columns):"
        in migration_text
    )
    assert "if index_name in _table_indexes(inspector, table_name):" in migration_text
    assert "ix_trailstep_course_user_complete" in migration_text
    assert "ix_discussion_community_pinned_date" in migration_text
    assert "ix_auditlog_org_id" in migration_text


def test_alembic_env_uses_runtime_database_url_for_docker_deployments():
    env_text = (API_ROOT / "migrations" / "env.py").read_text()

    assert "LEARNHOUSE_SQL_CONNECTION_STRING" in env_text
    assert '"postgresql+asyncpg://", "postgresql://", 1' in env_text
    assert 'config.set_main_option("sqlalchemy.url"' in env_text
    assert '.replace("%", "%%")' in env_text
    assert 'config.attributes.get("connection")' in env_text


def test_school_pilot_migration_aligns_assignment_learning_defaults():
    migration_text = (
        MIGRATIONS_DIR / "cd4e5f6a7b8c_add_school_pilot_assignment_fields.py"
    ).read_text()

    assert "ASSIGNMENT_PILOT_DEFAULTS" in migration_text
    assert "('auto_grading', sa.true(), True)" in migration_text
    assert "('show_correct_answers', sa.true(), True)" in migration_text
    assert "('allow_retries', sa.true(), True)" in migration_text
    assert "('max_retries', sa.text('0'), 0)" in migration_text
    assert "('score_policy', sa.text(\"'highest'\"), 'highest')" in migration_text
    assert "UPDATE assignment SET" not in migration_text


def test_assignment_json_hardening_migration_repairs_nulls_before_constraints():
    migration_text = (
        MIGRATIONS_DIR / "i0j1k2l3m4n5_harden_assignment_json_defaults.py"
    ).read_text()

    assert 'down_revision: Union[str, None] = "h9i0j1k2l3m4"' in migration_text
    assert "ASSIGNMENT_JSON_LIST_COLUMNS" in migration_text
    assert "SET {column_name} = '[]'::json" in migration_text
    assert "{column_name} IS NULL" in migration_text
    assert "{column_name}::jsonb = 'null'::jsonb" in migration_text
    assert 'nullable=False' in migration_text
    assert 'JSON_EMPTY_ARRAY_DEFAULT = sa.text("\'[]\'::json")' in migration_text
    assert "def downgrade()" in migration_text
    assert "nullable=True" in migration_text
    assert "server_default=None" in migration_text


def test_remediation_migration_uses_postgresql_safe_index_names():
    migration_text = (
        MIGRATIONS_DIR / "f7g8h9i0j1k2_add_assignment_remediation_practice.py"
    ).read_text()
    model_text = (API_ROOT / "src" / "db" / "courses" / "assignments.py").read_text()

    unsafe_name = (
        "ix_assignmentremediationpractice_assignment_remediation_practice_uuid"
    )
    safe_name = "ix_assignmentremediationpractice_uuid"

    assert unsafe_name not in migration_text
    assert unsafe_name not in model_text
    assert safe_name in migration_text
    assert safe_name in model_text
    assert len(safe_name) <= 63


def test_migrations_do_not_force_commit_inside_alembic_transactions():
    offenders = {
        path.name
        for path in MIGRATIONS_DIR.glob("*.py")
        if 'op.execute("COMMIT")' in path.read_text()
        or "op.execute('COMMIT')" in path.read_text()
    }

    assert offenders == set()


def test_rag_hnsw_migration_requires_pgvector_08_and_cosine_ops():
    migration_text = (
        MIGRATIONS_DIR / "g8h9i0j1k2l3_add_course_embedding_hnsw_index.py"
    ).read_text()

    assert 'down_revision: Union[str, None] = "f7g8h9i0j1k2"' in migration_text
    assert "pgvector >= 0.8.0" in migration_text
    assert "USING hnsw (embedding vector_cosine_ops)" in migration_text
    assert "op.execute(\"COMMIT\")" not in migration_text


def test_pdf_build_migration_matches_billing_period_and_course_lifecycle_model():
    migration_text = (
        MIGRATIONS_DIR / "h9i0j1k2l3m4_add_pdf_course_build_jobs.py"
    ).read_text()
    model_text = (API_ROOT / "src" / "db" / "pdf_course_build_jobs.py").read_text()

    assert 'down_revision: Union[str, None] = "g8h9i0j1k2l3"' in migration_text
    assert 'def _table_exists(table_name: str) -> bool:' in migration_text
    assert 'if _table_exists("pdf_course_build_job"):' in migration_text
    assert 'sa.Column("credit_period_token", sa.String(length=80)' in migration_text
    assert 'credit_period_token: str = Field(default="0"' in model_text
    assert 'sa.ForeignKeyConstraint(["course_id"], ["course.id"], ondelete="SET NULL")' in migration_text
    assert 'ForeignKey("course.id", ondelete="SET NULL")' in model_text


def test_pdf_build_migration_skips_table_created_by_fresh_metadata(monkeypatch):
    migration_path = (
        MIGRATIONS_DIR / "h9i0j1k2l3m4_add_pdf_course_build_jobs.py"
    )
    spec = importlib.util.spec_from_file_location("pdf_build_h9_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class ExistingTableInspector:
        @staticmethod
        def get_table_names():
            return ["pdf_course_build_job"]

    class FreshMetadataOps:
        @staticmethod
        def get_bind():
            return object()

        @staticmethod
        def create_table(*_args, **_kwargs):
            pytest.fail("h9 must not recreate the metadata-bootstrapped ledger")

    monkeypatch.setattr(migration, "op", FreshMetadataOps())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: ExistingTableInspector())

    migration.upgrade()


def test_pdf_build_claim_migration_matches_worker_eligibility_contract():
    migration_text = (
        MIGRATIONS_DIR / "h9i0j1k2l3m4_add_pdf_course_build_jobs.py"
    ).read_text()
    model_text = (API_ROOT / "src" / "db" / "pdf_course_build_jobs.py").read_text()
    service_text = (API_ROOT / "src" / "services" / "ai" / "pdf_build_jobs.py").read_text()

    for column_name in (
        "stage",
        "next_attempt_at",
        "lease_owner",
        "lease_expires_at",
        "created_at",
    ):
        assert f'sa.Column("{column_name}"' in migration_text
        assert f"{column_name}:" in model_text

    assert (
        'op.create_index(\n        "ix_pdf_build_claim",\n'
        '        "pdf_course_build_job",\n'
        '        ["stage", "next_attempt_at", "lease_expires_at", "created_at"],'
    ) in migration_text
    assert (
        'Index("ix_pdf_build_claim", "stage", "next_attempt_at", '
        '"lease_expires_at", "created_at")'
    ) in model_text
    assert "PDFCourseBuildJob.next_attempt_at <= now" in service_text
    assert "PDFCourseBuildJob.lease_expires_at <= now" in service_text
    assert ".with_for_update(skip_locked=True)" in service_text
