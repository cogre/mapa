"""Storage layer of the sample project."""
import sqlite3

SCHEMA = "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, total REAL)"


def connect(path=":memory:"):
    """Open the database and make sure the schema exists."""
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    return con


class Store:
    def __init__(self, con):
        self.con = con

    def save_order(self, total):
        self.con.execute("INSERT INTO orders (total) VALUES (?)", (total,))

    def unused_helper(self):
        return self.save_order(0)
