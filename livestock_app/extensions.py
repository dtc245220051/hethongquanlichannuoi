from sqlalchemy import MetaData, event
from sqlalchemy.engine import Engine

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

db = SQLAlchemy(metadata=metadata)
migrate = Migrate()


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(connection, _connection_record) -> None:
    if connection.__class__.__module__ != "sqlite3":
        return

    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
