"""Experiments with asyncio, each one an idea the patient monitor and the simulator will rely on.
Real sockets on 127.0.0.1, small timeouts, no database. Times have wide margins: the point is the
order of magnitude (0.2 s against 10 s), not the exact number."""

import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine

import pytest

from labs.asyncio_echo.echo import EchoServer, send


async def raw_client(port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", port)


# 1. The basics


def test_echo_round_trip() -> None:
    async def scenario() -> list[str]:
        server = EchoServer()
        port = await server.start()
        try:
            return await send(port, ["hello", "heart rate 72"])
        finally:
            await server.stop()

    assert asyncio.run(scenario()) == ["HELLO", "HEART RATE 72"]


# 2. One event loop, many connections: waiting does not block


def test_fifty_slow_clients_are_served_at_the_same_time() -> None:
    """Each reply waits 0.2 s with `await asyncio.sleep`. One after another, 50 replies would
    take 10 s; the loop runs the other connections while one waits, so it takes about 0.2 s."""

    async def scenario() -> float:
        server = EchoServer(work=lambda: asyncio.sleep(0.2))
        port = await server.start()
        started = time.perf_counter()
        try:
            replies = await asyncio.gather(*(send(port, [f"sensor {n}"]) for n in range(50)))
        finally:
            await server.stop()
        assert sorted(r[0] for r in replies) == sorted(f"SENSOR {n}" for n in range(50))
        return time.perf_counter() - started

    assert asyncio.run(scenario()) < 2.0


def test_a_blocking_call_stops_every_connection() -> None:
    """The same work with `time.sleep(0.1)`: it never gives control back, so 10 clients wait for
    each other, at least 10 x 0.1 s. A blocking database call in a handler would do the same."""

    async def scenario() -> float:
        server = EchoServer(blocking_work_seconds=0.1)
        port = await server.start()
        started = time.perf_counter()
        try:
            await asyncio.gather(*(send(port, [f"sensor {n}"]) for n in range(10)))
        finally:
            await server.stop()
        return time.perf_counter() - started

    assert asyncio.run(scenario()) >= 1.0


# 3. TCP is a stream of bytes, not of messages (protocol.md, framing)


def test_one_message_can_arrive_in_pieces_and_two_in_one_piece() -> None:
    """The client writes "hel", then "lo\\nwor", then "ld\\n". readline still gives whole lines:
    the server cannot rely on one write being one read."""

    async def scenario() -> list[bytes]:
        server = EchoServer()
        port = await server.start()
        reader, writer = await raw_client(port)
        try:
            for piece in (b"hel", b"lo\nwor", b"ld\n"):
                writer.write(piece)
                await writer.drain()
                await asyncio.sleep(0.05)  # so the pieces really travel apart
            return [await reader.readline(), await reader.readline()]
        finally:
            writer.close()
            await server.stop()

    assert asyncio.run(scenario()) == [b"HELLO\n", b"WORLD\n"]


def test_read_n_returns_what_has_arrived_not_n_bytes() -> None:
    """read(100) returns as soon as some bytes are there; readexactly(n) waits for all n. The
    patient monitor reads a 4 byte length, then exactly that many bytes (P-01)."""

    async def scenario() -> tuple[bytes, bytes]:
        server = EchoServer()
        port = await server.start()
        reader, writer = await raw_client(port)
        try:
            writer.write(b"abc\n")
            await writer.drain()
            first = await reader.read(100)
            writer.write(b"defgh\n")
            await writer.drain()
            exact = await reader.readexactly(3)
            return first, exact
        finally:
            writer.close()
            await server.stop()

    assert asyncio.run(scenario()) == (b"ABC\n", b"DEF")


# 4. Timeouts


def test_a_silent_client_is_disconnected() -> None:
    """The server waits at most idle_timeout for the next line, then closes (protocol.md: a
    silent connection is closed after 15 s; 0.2 s here)."""

    async def scenario() -> tuple[bytes, int]:
        server = EchoServer(idle_timeout=0.2)
        port = await server.start()
        reader, writer = await raw_client(port)
        try:
            async with asyncio.timeout(2):
                end = await reader.read()  # b"" once the server has closed
            return end, server.cleaned_up
        finally:
            writer.close()
            await server.stop()

    assert asyncio.run(scenario()) == (b"", 1)


def test_a_reply_that_never_comes_is_a_timeout_error() -> None:
    """The client gives up after reply_timeout (a sensor resends after 3 s without an ACK)."""

    async def scenario() -> float:
        server = EchoServer(work=lambda: asyncio.sleep(10))
        port = await server.start()
        started = time.perf_counter()
        try:
            with pytest.raises(TimeoutError):
                await send(port, ["hello"], reply_timeout=0.2)
        finally:
            await server.stop()
        return time.perf_counter() - started

    assert asyncio.run(scenario()) < 2.0


def test_a_timeout_cancels_the_work_and_its_cleanup_still_runs() -> None:
    steps: list[str] = []

    async def work() -> None:
        try:
            steps.append("started")
            await asyncio.sleep(10)
            steps.append("finished")  # never reached
        finally:
            steps.append("cleaned up")

    async def scenario() -> None:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(0.1):
                await work()

    asyncio.run(scenario())
    assert steps == ["started", "cleaned up"]


# 5. Cancellation


def test_stop_cancels_every_open_connection_and_each_cleans_up() -> None:
    async def scenario() -> tuple[int, list[bytes]]:
        server = EchoServer()
        port = await server.start()
        clients = [await raw_client(port) for _ in range(5)]
        await asyncio.sleep(0.05)  # let the server accept them
        await server.stop()
        ends = [await reader.read() for reader, _ in clients]
        for _, writer in clients:
            writer.close()
        return server.cleaned_up, ends

    assert asyncio.run(scenario()) == (5, [b""] * 5)


def test_swallowing_cancellation_keeps_a_task_alive() -> None:
    """A task that catches CancelledError and goes on cannot be stopped: shutdown would hang.
    The fixed version cleans up and raises it again.

    The first two drafts of this test hung the whole test run, which is the lesson itself:
    asyncio.run cancels the tasks still alive when it ends and waits for them, and the stubborn
    task ignored that too. It now has a flag of its own, so the experiment can end it after
    showing that cancel() could not."""
    give_up = False

    async def stubborn() -> None:
        while not give_up:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue  # the mistake

    async def polite() -> None:
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            # clean up here, then let cancellation go on
            raise

    async def scenario(body: Callable[[], Coroutine[None, None, None]]) -> str:
        nonlocal give_up
        give_up = False
        task = asyncio.create_task(body())
        await asyncio.sleep(0.01)
        task.cancel()
        # asyncio.wait waits without cancelling what it waits for. `async with asyncio.timeout`
        # around `await task` would hang here too: on timeout it cancels the waiting task, that
        # cancel is passed on to `task`, and `task` swallows it again (second draft, d246).
        done, _ = await asyncio.wait({task}, timeout=0.3)
        if not done:
            give_up = True  # the only way left to end it
            await task
            return "still running after cancel()"
        return "cancelled" if task.cancelled() else "finished"

    assert asyncio.run(scenario(stubborn)) == "still running after cancel()"
    assert asyncio.run(scenario(polite)) == "cancelled"


def test_a_task_group_cancels_the_siblings_of_a_failed_task() -> None:
    """In a TaskGroup one failure cancels the rest. Good for parts of one job; wrong for sensor
    connections, where one broken sensor must not drop the others. The monitor will therefore
    give each connection its own task and handle its errors there."""
    finished: list[str] = []

    async def sensor(name: str, fail_after: float | None) -> None:
        await asyncio.sleep(fail_after if fail_after is not None else 0.3)
        if fail_after is not None:
            raise ConnectionResetError(name)
        finished.append(name)

    async def scenario() -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(sensor("SN-PO-0001", None))
            group.create_task(sensor("SN-RR-0001", 0.05))

    with pytest.raises(ExceptionGroup):
        asyncio.run(scenario())
    assert finished == []
