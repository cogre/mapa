"""Reads orders and prints a total."""
from core import db
from core.db import connect


def total(con):
    return con.execute("SELECT SUM(total) FROM orders").fetchone()[0]


def run():
    con = connect()
    store = db.Store(con)
    store.save_order(10)
    return total(con)


def never_called():
    return 1
