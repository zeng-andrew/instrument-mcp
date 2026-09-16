"""MXA N9020A 文件导出工具。

用法:
  python mxa_file_probe.py walk            # 遍历目录树
  python mxa_file_probe.py find 0911       # 查找文件名含关键字的文件
  python mxa_file_probe.py pull <远端路径> <本地路径>   # 导出单个文件

SCPI over 5025，ASCII 查询按行读取（响应以 \n 结束且连接不关闭）。
MMEM:DATA? 为 IEEE definite-length block，按头部长度精确读取。
"""
import csv
import io
import socket
import sys
import time

HOST, PORT = "172.22.1.4", 5025

SKIP_DIRS = {
    "[$RECYCLE.BIN]", "[System Volume Information]", "[AppBin7.2]",
    "[NVARB]", "[Windows]", "[Program Files]", "[Program Files (x86)]",
    "[ProgramData]", "[PerfLogs]",
}


class MxaClient:
    def __init__(self, host=HOST, port=PORT, timeout=15.0):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(timeout)

    def close(self):
        self.sock.close()

    def cmd(self, scpi):
        self.sock.sendall(scpi.encode("ascii") + b"\n")

    def query_line(self, scpi, timeout=15.0):
        """ASCII 查询：读到行尾 \\n 为止。"""
        self.cmd(scpi)
        self.sock.settimeout(timeout)
        buf = bytearray()
        while not buf.endswith(b"\n"):
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed by instrument")
            buf += chunk
        return bytes(buf).decode("utf-8", errors="replace").rstrip("\r\n")

    def query_block(self, scpi, timeout=120.0):
        """MMEM:DATA? 查询：解析 definite-length block 并返回纯数据字节。

        块头形如 #9<9位长度>，其后紧跟数据，无换行；数据后有一个 \\n。
        """
        self.cmd(scpi)
        self.sock.settimeout(timeout)

        def read_exact(n):
            buf = bytearray()
            while len(buf) < n:
                chunk = self.sock.recv(min(65536, n - len(buf)))
                if not chunk:
                    raise ConnectionError("connection closed mid-block")
                buf += chunk
            return bytes(buf)

        first = read_exact(1)
        if first != b"#":
            rest = bytearray(first)
            self.sock.settimeout(5)
            while not rest.endswith(b"\n"):
                c = self.sock.recv(1)
                if not c:
                    break
                rest += c
            raise ValueError(f"非块响应: {bytes(rest)[:80]!r}")
        ndigits = int(read_exact(1))
        blen = int(read_exact(ndigits))
        body = read_exact(blen)
        tail = bytearray()
        self.sock.settimeout(3)
        try:
            while not tail.endswith(b"\n"):
                c = self.sock.recv(1)
                if not c:
                    break
                tail += c
        except socket.timeout:
            pass
        return body

    def cat(self, path, long_fmt=False):
        verb = "MMEM:CAT:LONG?" if long_fmt else "MMEM:CAT?"
        raw = self.query_line(f'{verb} "{path}"')
        if not raw or raw.startswith("-"):
            return []
        rows = list(csv.reader(io.StringIO(raw)))
        entries = rows[0][2:]  # 跳过 free/total 摘要
        out = []
        for e in entries:
            if not e or not e[0]:
                continue
            out.append(e)
        return out


def walk(cl, path="/", depth=0, maxdepth=5, prefix=""):
    for e in cl.cat(path):
        name = e[0]
        if name in SKIP_DIRS:
            continue
        isdir = name.startswith("[")
        disp = name if isdir else f"{name}  ({','.join(e[1:3])})"
        print(f"{prefix}{disp}")
        if isdir and depth < maxdepth:
            walk(cl, path.rstrip("/") + "/" + name.strip("[]"),
                 depth + 1, maxdepth, prefix + "  ")


def find(cl, keyword):
    hits = []

    def rec(path, depth=0):
        if depth > 5:
            return
        for e in cl.cat(path):
            name = e[0]
            if name in SKIP_DIRS:
                continue
            full = path.rstrip("/") + "/" + name.strip("[]")
            if keyword.lower() in name.lower():
                hits.append((full, e))
                print(f"HIT {full}  {','.join(e[1:])}")
            if name.startswith("["):
                rec(full, depth + 1)

    rec("/")
    return hits


def pull(cl, remote, local):
    t0 = time.time()
    data = cl.query_block(f'MMEM:DATA? "{remote}"', 120)
    with open(local, "wb") as f:
        f.write(data)
    print(f"OK {remote} -> {local}  ({len(data)} bytes, {time.time()-t0:.1f}s)")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "walk"
    cl = MxaClient()
    try:
        if mode == "idn":
            print(cl.query_line("*IDN?"))
        elif mode == "walk":
            walk(cl)
        elif mode == "find":
            find(cl, sys.argv[2] if len(sys.argv) > 2 else "0911")
        elif mode == "pull":
            pull(cl, sys.argv[2], sys.argv[3])
        elif mode == "cat":
            for e in cl.cat(sys.argv[2], long_fmt="--long" in sys.argv):
                print(",".join(e))
        else:
            print(__doc__)
    finally:
        cl.close()


if __name__ == "__main__":
    main()
