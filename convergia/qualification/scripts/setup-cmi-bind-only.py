"""Private loopback jCli fixture, extracted from the RDT29 harness."""
import socket
import time
import re

class Console:
    def __init__(self, username, password):
        deadline = time.monotonic() + 30
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", 8990), timeout=3)
                break
            except (ConnectionRefusedError, socket.timeout):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Management console did not become ready")
                time.sleep(1)
        self.sock.settimeout(1)
        self.buf = b""
        self.wire = b""
        self.until("Username:", timeout=3, soft=True)
        self.send(username)
        self.until("Password:", timeout=3, soft=True)
        self.send(password)
        self.until("jcli : ")

    def send(self, line):
        if "\n" in line or "\r" in line:
            raise RuntimeError("Invalid console input")
        self.sock.sendall(line.encode("ascii") + b"\n")

    def until(self, marker, timeout=15, soft=False):
        deadline = time.monotonic() + timeout
        marker = marker.encode("ascii")
        while marker not in self.buf:
            if time.monotonic() > deadline:
                if soft:
                    self.buf = b""
                    return ""
                raise RuntimeError("Console response timed out")
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                raise RuntimeError("Console connection closed")
            self.wire += chunk
            while self.wire:
                if self.wire[0] != 255:
                    self.buf += self.wire[:1]
                    self.wire = self.wire[1:]
                elif len(self.wire) < 2:
                    break
                elif self.wire[1] in (251, 252, 253, 254):
                    if len(self.wire) < 3:
                        break
                    cmd, option = self.wire[1:3]
                    if cmd in (251, 253):
                        self.sock.sendall(bytes([255, 254 if cmd == 251 else 252, option]))
                    self.wire = self.wire[3:]
                elif self.wire[1] == 250:
                    end = self.wire.find(b"\xff\xf0", 2)
                    if end == -1:
                        break
                    self.wire = self.wire[end + 2:]
                elif self.wire[1] == 255:
                    self.buf += b"\xff"
                    self.wire = self.wire[2:]
                else:
                    self.wire = self.wire[2:]
        end = self.buf.index(marker) + len(marker)
        response, self.buf = self.buf[:end], self.buf[end:]
        return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", response.decode("ascii", errors="replace"))

    def command(self, command):
        self.send(command)
        return self.until("jcli : ")

    def edit(self, command, settings, success):
        self.send(command)
        self.until("> ")
        for setting in settings:
            self.send(setting)
            response = self.until("> ")
            if re.search(r"unknown|invalid|error|cannot", response, re.I):
                self.send("ko")
                self.until("jcli : ")
                raise RuntimeError("Console rejected an account setting")
        self.send("ok")
        response = self.until("jcli : ")
        if success not in response:
            raise RuntimeError("Account configuration was not saved")

    def close(self):
        self.sock.close()
