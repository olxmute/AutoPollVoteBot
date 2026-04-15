from yoyo import get_backend, read_migrations


def apply_migrations(db_path: str) -> None:
    backend = get_backend(f'sqlite:///{db_path}')
    migrations = read_migrations('migrations/')
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))
