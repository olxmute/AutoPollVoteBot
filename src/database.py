from pathlib import Path

from yoyo import get_backend, read_migrations


def apply_migrations(db_path: str) -> None:
    backend = get_backend(f'sqlite:///{db_path}')
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    migrations = read_migrations(str(migrations_dir))
    if not migrations:
        raise RuntimeError("No migrations found in migrations/")
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))
