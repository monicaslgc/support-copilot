"""Process-wide state shared by the routes and the services: the database, the knowledge base and the
ticket-system adapters.

Read these as attributes (`state.conn`), never `from .state import conn`: the tests, and any future app
factory, replace them, and a copied reference would keep pointing at the old object.
"""
import threading

from .db import connect
from .kb import KnowledgeBase
from .ticketsystems import TicketSystem, build_systems

kb = KnowledgeBase()
conn = connect()
systems: dict[str, TicketSystem] = build_systems()

# One SQLite connection is shared by the worker threads and it is not safe for simultaneous use, reads
# included (concurrent calls raise InterfaceError). Every DB access takes this lock; it is never held
# across a network or model call.
lock = threading.RLock()


def fetch_all(sql: str, params: tuple = ()) -> list:
    with lock:
        return conn.execute(sql, params).fetchall()


def fetch_one(sql: str, params: tuple = ()):
    with lock:
        return conn.execute(sql, params).fetchone()
