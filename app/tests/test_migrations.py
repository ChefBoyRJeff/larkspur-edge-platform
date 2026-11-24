from collections.abc import Callable

from alembic import command
from alembic.config import Config


def test_migrations_upgrade_downgrade_upgrade(
    use_database: Callable[[str], None], test_database_url: str, alembic_config: Config
) -> None:
    use_database(test_database_url)

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


def test_models_match_migrations(
    use_database: Callable[[str], None], test_database_url: str, alembic_config: Config
) -> None:
    """Fails when a model changed without a matching migration."""
    use_database(test_database_url)

    command.upgrade(alembic_config, "head")
    command.check(alembic_config)
