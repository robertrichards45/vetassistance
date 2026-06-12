from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import MetaData, create_engine, func, select, text
from sqlalchemy.engine import Engine

from app.models import Base


@dataclass
class TableMigrationResult:
    table: str
    rows: int


def _source_engine(sqlite_path: str | Path) -> Engine:
    path = Path(sqlite_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    return create_engine(f"sqlite:///{path.as_posix()}", future=True)


def _ordered_tables(source_meta: MetaData) -> list[str]:
    model_tables = [table.name for table in Base.metadata.sorted_tables if table.name in source_meta.tables]
    remaining = sorted(name for name in source_meta.tables.keys() if name not in model_tables)
    return model_tables + remaining


def _chunked(rows: list[dict], batch_size: int) -> Iterable[list[dict]]:
    for idx in range(0, len(rows), batch_size):
        yield rows[idx : idx + batch_size]


def _truncate_postgres_tables(engine: Engine, table_names: list[str]) -> None:
    preparer = engine.dialect.identifier_preparer
    quoted = ", ".join(preparer.quote(name) for name in table_names)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


def _reset_postgres_sequences(engine: Engine, table_names: list[str]) -> None:
    preparer = engine.dialect.identifier_preparer
    metadata = MetaData()
    metadata.reflect(bind=engine, only=table_names)

    with engine.begin() as conn:
        for table_name in table_names:
            table = metadata.tables.get(table_name)
            if table is None:
                continue
            for column in table.columns:
                if not column.primary_key:
                    continue
                sequence_name = conn.execute(
                    text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                    {"table_name": table_name, "column_name": column.name},
                ).scalar_one_or_none()
                if not sequence_name:
                    continue
                quoted_table = preparer.quote(table_name)
                quoted_column = preparer.quote(column.name)
                max_value = conn.execute(
                    text(f"SELECT COALESCE(MAX({quoted_column}), 0) FROM {quoted_table}")
                ).scalar_one()
                if max_value and int(max_value) > 0:
                    conn.execute(
                        text("SELECT setval(:sequence_name, :max_value, true)"),
                        {"sequence_name": sequence_name, "max_value": int(max_value)},
                    )
                else:
                    conn.execute(
                        text("SELECT setval(:sequence_name, 1, false)"),
                        {"sequence_name": sequence_name},
                    )


def migrate_sqlite_to_postgres(
    sqlite_path: str | Path,
    target_engine: Engine,
    *,
    truncate: bool = True,
    batch_size: int = 500,
    dry_run: bool = False,
) -> list[TableMigrationResult]:
    if target_engine.url.get_backend_name() == "sqlite":
        raise ValueError("Target engine must be PostgreSQL, not SQLite.")

    Base.metadata.create_all(bind=target_engine)

    source_engine = _source_engine(sqlite_path)
    source_meta = MetaData()
    source_meta.reflect(bind=source_engine)
    table_names = _ordered_tables(source_meta)

    if truncate and not dry_run and table_names:
        _truncate_postgres_tables(target_engine, table_names)

    target_meta = MetaData()
    target_meta.reflect(bind=target_engine, only=table_names)

    results: list[TableMigrationResult] = []

    with source_engine.connect() as source_conn:
        for table_name in table_names:
            source_table = source_meta.tables[table_name]
            rows = [dict(row) for row in source_conn.execute(select(source_table)).mappings()]
            results.append(TableMigrationResult(table=table_name, rows=len(rows)))

            if dry_run or not rows:
                continue

            target_table = target_meta.tables.get(table_name)
            if target_table is None:
                continue

            with target_engine.begin() as target_conn:
                for chunk in _chunked(rows, batch_size):
                    target_conn.execute(target_table.insert(), chunk)

    if not dry_run and table_names:
        _reset_postgres_sequences(target_engine, table_names)

    return results


def count_rows(engine: Engine, table_names: Iterable[str]) -> dict[str, int]:
    metadata = MetaData()
    names = list(table_names)
    metadata.reflect(bind=engine, only=names)

    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table_name in names:
            table = metadata.tables.get(table_name)
            if table is None:
                counts[table_name] = 0
                continue
            counts[table_name] = conn.execute(select(func.count()).select_from(table)).scalar_one()
    return counts
