"""PTY transport: shared output, bounded nonblocking input and terminal sizing."""
from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import struct
import termios


class Terminal:
    def __init__(self, master, proc):
        self.master, self.proc = master, proc
        self.closed = False
        self.buffer = bytearray()
        self.eof = False
        os.set_blocking(master, False)
        self.stdout = self
        self.stdin = self

    def __getattr__(self, key):
        return getattr(self.proc, key)

    def is_closing(self):
        return self.closed or self.eof or self.proc.returncode is not None

    def write(self, data):
        if self.is_closing():
            raise BrokenPipeError("Terminal input is closed")
        if len(self.buffer) + len(data) > 1024 * 1024:
            raise ValueError("Terminal input queue is full")
        self.buffer.extend(data)

    async def _ready(self, write=False):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        def wake():
            if not future.done():
                future.set_result(None)
        (loop.add_writer if write else loop.add_reader)(self.master, wake)
        try:
            await future
        finally:
            (loop.remove_writer if write else loop.remove_reader)(self.master)

    async def drain(self):
        while self.buffer and not self.closed:
            try:
                count = os.write(self.master, self.buffer)
                del self.buffer[:count]
            except BlockingIOError:
                await self._ready(write=True)

    async def read(self, size):
        while not self.closed:
            try:
                return os.read(self.master, size)
            except BlockingIOError:
                await self._ready()
            except OSError as exc:
                if exc.errno == errno.EIO:  # Last slave descriptor closed.
                    return b""
                raise
        return b""

    def close(self):
        # Canonical terminal EOF, without closing the shared output descriptor.
        if not self.is_closing():
            os.write(self.master, b"\x04")
            self.eof = True

    def resize(self, rows=24, cols=80):
        if self.closed:
            raise BrokenPipeError("Terminal is closed")
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def dispose(self):
        if not self.closed:
            self.closed = True
            os.close(self.master)
