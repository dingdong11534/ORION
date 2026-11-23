from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from orion.infrastructure.persistence.models import Base
from orion.infrastructure.persistence.paths import ensure_secure_directory, get_database_path

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Resolve database URL using application paths and ORION_DB_PATH override."""
    # Check if explicitly provided via CLI -x or ini
    x_args = context.get_x_argument(as_dictionary=True)
    if "db_path" in x_args:
        db_path = get_database_path(x_args["db_path"])
        ensure_secure_directory(db_path.parent)
        return f"sqlite:///{db_path}"

    url = config.get_main_option("sqlalchemy.url")
    if url and url.strip():
        return url

    db_path = get_database_path()
    ensure_secure_directory(db_path.parent)
    return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
