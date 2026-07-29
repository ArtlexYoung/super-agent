"""Optional remote SQL storage backends."""

from adapter.storage.sql.mysql import MySqlStorage
from adapter.storage.sql.postgresql import PostgreSqlStorage

__all__ = ["MySqlStorage", "PostgreSqlStorage"]
