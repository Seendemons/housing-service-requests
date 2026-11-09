import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine


@pytest.fixture
def reject_nul_sql_parameters():
    """SQLite допускает NUL, поэтому отдельно запрещаем его в SQL-параметрах."""

    def check_parameters(conn, cursor, statement, parameters, context, executemany):
        rows = parameters if executemany else [parameters]
        for row in rows:
            values = row.values() if isinstance(row, dict) else row
            for value in values:
                if isinstance(value, str):
                    assert "\x00" not in value, "Нулевой символ передан в БД"

    event.listen(Engine, "before_cursor_execute", check_parameters)
    try:
        yield
    finally:
        event.remove(Engine, "before_cursor_execute", check_parameters)
