"""G1: 50 simultaneous inserts of one slot, each with its own connection and transaction."""

import threading

import psycopg
from psycopg import errors

from .conftest import APP_URL, insert_booking, local_time

THREADS = 50


def test_exactly_one_of_fifty_concurrent_inserts_wins(ids):
    # Connect first, so the barrier releases all the inserts at the same moment.
    connections = [psycopg.connect(APP_URL, autocommit=True) for _ in range(THREADS)]
    barrier = threading.Barrier(THREADS)
    results: list[str] = []
    lock = threading.Lock()

    def attempt(conn: psycopg.Connection) -> None:
        barrier.wait()
        try:
            insert_booking(
                conn,
                ids["acme"],
                ids["Kiwi"],
                ids["alice@acme.test"],
                local_time(14),
                local_time(14, 30),
            )
            outcome = "created"
        except errors.ExclusionViolation:
            outcome = "conflict"
        except psycopg.Error as exc:
            outcome = type(exc).__name__
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt, args=(conn,)) for conn in connections]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for conn in connections:
        conn.close()

    assert results.count("created") == 1
    assert results.count("conflict") == THREADS - 1
