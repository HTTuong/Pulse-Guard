# Labs

Small programs for learning a technique before it is used in PulseGuard. They are not part of the product: the Docker image does not contain them. Each lab has tests in `tests/labs/` that run its experiments, so what it teaches is checked, not only written down.

## asyncio_echo (d246)

A TCP echo server and client, to learn asyncio before the patient monitor (d248) and the sensor simulator (d247).

```bash
python -m labs.asyncio_echo.echo serve --port 7700            # in one terminal
python -m labs.asyncio_echo.echo send --port 7700 hello world  # in another: HELLO, WORLD
```

| Idea | What the experiment shows (`tests/labs/test_asyncio_echo.py`) | Where PulseGuard uses it |
|---|---|---|
| One event loop, one task per connection | 50 clients whose replies each wait 0.2 s with `await asyncio.sleep` are served in about 0.2 s, not 10 s (0.21 s measured on 2026-11-20) | The monitor serves several sensors at once |
| Never block the loop | The same work with `time.sleep(0.1)` makes 10 clients wait for each other: at least 1 s (1.01 s measured) | Database calls must not run inside the monitor's event loop |
| TCP is a byte stream | "hel", "lo\nwor", "ld\n" written apart still come out as two lines; `read(100)` returns what has arrived, `readexactly(n)` waits for all n | Framing with a length prefix (protocol.md, P-01 to P-03) |
| Timeouts | A client silent for longer than the idle timeout is disconnected; a reply that never comes raises `TimeoutError`; the work inside a timeout is cancelled and its `finally` still runs | Silent connection closed after 15 s; resend after 3 s without an ACK |
| Cancellation | Stopping the server cancels every connection and each one cleans up; a task that swallows `CancelledError` cannot be stopped | Clean shutdown of the monitor and the simulator |
| TaskGroup | One failing task cancels its siblings | Not for sensor connections: one broken sensor must not drop the others |

What went wrong while writing it, and is part of the lesson:

- The test of a task that swallows cancellation hung the whole test run twice. First, `asyncio.run` cancels leftover tasks at the end and waits for them, and the stubborn task ignored that too. Then `async with asyncio.timeout` around `await task` hung as well: on timeout it cancels the waiting task, that cancel is passed on to the awaited task, and the stubborn task swallowed it again. The test now waits with `asyncio.wait(..., timeout=...)`, which does not cancel what it waits for, and gives the stubborn task a flag of its own to stop.
