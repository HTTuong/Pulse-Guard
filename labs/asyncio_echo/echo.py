"""A small TCP echo server and client, to learn asyncio before the patient monitor (d248).

Each idea here comes back in PulseGuard:
- one task per connection: the monitor serves several sensors at once (protocol.md, Roles)
- reading lines from a stream: TCP is a stream of bytes, not of messages (P-01 to P-03)
- an idle timeout: a silent connection is closed after 15 s (protocol.md, Timeouts)
- a timeout on a reply: a sensor resends a reading with no ACK after 3 s (P-16)
- cancellation on shutdown: every connection is closed, and its cleanup still runs

Run it by hand:
    python -m labs.asyncio_echo.echo serve --port 7700
    python -m labs.asyncio_echo.echo send --port 7700 hello world
"""

import argparse
import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger("echo")


@dataclass
class EchoServer:
    """Echoes every line back, in upper case so a test can tell the echo from the request."""

    idle_timeout: float = 15.0
    # A hook to make each reply slow, for the experiments: awaited (non blocking) or not.
    work: Callable[[], Awaitable[None]] | None = None
    blocking_work_seconds: float = 0.0
    handlers: set[asyncio.Task[None]] = field(default_factory=set)
    cleaned_up: int = 0
    server: asyncio.Server | None = None

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self.server = await asyncio.start_server(self._accept, host, port)
        bound: int = self.server.sockets[0].getsockname()[1]
        return bound

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # asyncio runs this coroutine as its own task for every connection; keeping the tasks
        # lets stop() cancel the ones still open.
        task = asyncio.current_task()
        if task is None:  # always set inside a running task; this keeps the type checker sure
            raise RuntimeError("no current task")
        self.handlers.add(task)
        try:
            await self._serve(reader, writer)
        finally:
            self.handlers.discard(task)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            while True:
                try:
                    async with asyncio.timeout(self.idle_timeout):
                        line = await reader.readline()
                except TimeoutError:
                    log.info("closing %s: silent for %.1f s", peer, self.idle_timeout)
                    return
                if not line:  # the client closed its side
                    return
                if self.work is not None:
                    await self.work()
                if self.blocking_work_seconds:
                    time.sleep(self.blocking_work_seconds)  # on purpose: blocks the whole loop
                writer.write(line.upper())
                await writer.drain()
        finally:
            # Runs on a normal end, on a timeout and on cancellation alike.
            self.cleaned_up += 1
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def stop(self) -> None:
        """Stop accepting, cancel every open connection, wait until each has cleaned up."""
        if self.server is not None:
            self.server.close()
        for task in list(self.handlers):
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        if self.server is not None:
            await self.server.wait_closed()


async def send(
    port: int, lines: list[str], reply_timeout: float = 3.0, host: str = "127.0.0.1"
) -> list[str]:
    """Send each line and wait for its echo; TimeoutError if one takes over reply_timeout."""
    reader, writer = await asyncio.open_connection(host, port)
    replies = []
    try:
        for line in lines:
            writer.write(line.encode() + b"\n")
            await writer.drain()
            async with asyncio.timeout(reply_timeout):
                reply = await reader.readline()
            replies.append(reply.decode().rstrip("\n"))
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
    return replies


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m labs.asyncio_echo.echo")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=7700)
    client = commands.add_parser("send")
    client.add_argument("--port", type=int, default=7700)
    client.add_argument("lines", nargs="+")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.command == "send":
        for reply in asyncio.run(send(args.port, args.lines)):
            print(reply)
        return

    async def run() -> None:
        server = EchoServer()
        port = await server.start(port=args.port)
        log.info("listening on %s; Ctrl+C to stop", port)
        try:
            await asyncio.Event().wait()  # until cancelled by Ctrl+C
        finally:
            await server.stop()
            log.info("stopped; %s connections cleaned up", server.cleaned_up)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
