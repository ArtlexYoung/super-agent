"""Optional remote SQL storage backends."""

from core.storage.sql.mysql import MySqlStorage
from core.storage.sql.postgresql import PostgreSqlStorage

__all__ = ["MySqlStorage", "PostgreSqlStorage"]
