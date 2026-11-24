from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config

APP_DIR = Path(__file__).resolve().parents[1]


def test_migrations_upgrade_downgrade_upgrade(
    use_database: Callable[[str], None], test_database_url: str
) -> None:
    use_database(test_database_url)
    config = Config(str(APP_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(APP_DIR / "migrations"))

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
