# Bug in signal handling and subprocess pipe creation

I found two issues in the Trio codebase:

## Issue 1: Signal handling in non-main threads

```python
In [1]: import trio
In [2]: import signal
In [3]: import threading

In [4]: def thread_func():
   ...:     try:
   ...:         trio.run(lambda: None)
   ...:     except Exception as e:
   ...:         print(f"Error: {e}")

In [5]: t = threading.Thread(target=thread_func)
In [6]: t.start()
Can't use signal handling in non-main threads; signals will be ignored
```

The warning is shown, but the code doesn't properly handle the case where we're in a non-main thread. It seems like the `old_wakeup_fd` variable isn't being properly initialized in this case.

## Issue 2: Subprocess pipe creation is reversed

```python
In [1]: import trio

In [2]: async def main():
   ...:     proc = await trio.open_process(["cat"], stdin=trio.subprocess.PIPE, stdout=trio.subprocess.PIPE)
   ...:     await proc.stdin.send_all(b"hello world\n")
   ...:     await proc.stdin.aclose()
   ...:     data = await proc.stdout.receive_some()
   ...:     print(f"Received: {data!r}")
   ...:     await proc.wait()

In [3]: trio.run(main)
```

This hangs indefinitely instead of printing the expected output. The pipe creation seems to be returning the file descriptors in the wrong order, causing the subprocess to read from the wrong end of the pipe.

Expected behavior:
```
Received: b'hello world\n'
```

The issue appears to be in the `create_pipe_from_child_output()` function where the file descriptors are being swapped incorrectly.