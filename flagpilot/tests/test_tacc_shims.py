"""课程运行时的工具替身（deploy/tcloud/client/bin/）：xxd 与 exiftool。

TACC 作业 29323 的 doctor 报 exiftool 影响 5 道取证题，模型在 forensics-003 第一步就敲了 xxd 撞上
`xxd: not found`。替身必须"像真的一样"：xxd 与系统 xxd 逐字节对拍；exiftool 用手搓的
JPEG/PNG/GIF/PDF 样本，确认 CTF 常藏 flag 的字段都读得出来。
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "deploy" / "tcloud" / "client" / "bin"
FLAG = "picoCTF{m3t4d4t4_h1d3s_th1ngs}"


def run(tool, *args, stdin=b"", cwd=None):
    return subprocess.run([sys.executable, str(BIN / tool), *args], input=stdin, cwd=cwd,
                          capture_output=True, timeout=30)


class TestXxd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-xxd-"))
        (self.tmp / "a.bin").write_bytes(bytes(range(256)) * 2 + b"hello world\n")
        (self.tmp / "b.txt").write_bytes(b"picoCTF{n0t_4ll}\n\xe2\x80\x83 x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(shutil.which("xxd"), "本机没有系统 xxd 可对拍")
    def test_byte_identical_to_system_xxd(self):
        for args in ([], ["-p"], ["-c", "8"], ["-g", "1"], ["-l", "20"], ["-s", "4"], ["-u"],
                     ["-c", "12", "-g", "4"], ["-b"], ["-s", "10", "-l", "33"], ["-i"], ["-g", "0"], ["-c8"]):
            for f in ("a.bin", "b.txt"):
                want = subprocess.run(["xxd", *args, f], cwd=self.tmp, capture_output=True).stdout
                got = run("xxd", *args, f, cwd=self.tmp).stdout
                self.assertEqual(got, want, f"xxd {' '.join(args)} {f}")

    def test_revert_round_trips(self):
        for f in ("a.bin", "b.txt"):
            data = (self.tmp / f).read_bytes()
            dump = run("xxd", f, cwd=self.tmp).stdout
            self.assertEqual(run("xxd", "-r", stdin=dump).stdout, data)
            plain = run("xxd", "-p", f, cwd=self.tmp).stdout
            self.assertEqual(run("xxd", "-r", "-p", stdin=plain).stdout, data)

    def test_unknown_option_fails_loudly(self):
        r = run("xxd", "-E", "a.bin", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"unsupported option", r.stderr)


def _ifd(entries, base, extra_after=b""):
    """小端 TIFF IFD：entries=[(tag, type, count, value_bytes)]，长值放到 IFD 之后。"""
    n = len(entries)
    data_off = base + 2 + 12 * n + 4
    head, tail = struct.pack("<H", n), b""
    for tag, typ, cnt, val in entries:
        if len(val) <= 4:
            head += struct.pack("<HHI", tag, typ, cnt) + val.ljust(4, b"\x00")
        else:
            head += struct.pack("<HHII", tag, typ, cnt, data_off + len(tail))
            tail += val + (b"\x00" if len(val) % 2 else b"")
    return head + struct.pack("<I", 0) + tail + extra_after


def make_jpeg():
    # IFD0 → ExifIFD（UserComment）与 GPS（纬度）
    desc = b"secret notes\x00"
    xp = "XP " + FLAG
    ifd0_entries = [(0x010E, 2, len(desc), desc), (0x013B, 2, 6, b"alice\x00"),
                    (0x9C9C, 1, len(xp.encode("utf-16-le")) + 2, xp.encode("utf-16-le") + b"\x00\x00"),
                    (0x8769, 4, 1, b"\x00\x00\x00\x00"), (0x8825, 4, 1, b"\x00\x00\x00\x00"),
                    (0xBEEF, 2, 11, b"odd-field!\x00")]
    ifd0 = _ifd(ifd0_entries, 8)
    exif_off = 8 + len(ifd0)
    uc = b"ASCII\x00\x00\x00" + b"hello from user comment"
    exif_ifd = _ifd([(0x9286, 7, len(uc), uc)], exif_off)
    gps_off = exif_off + len(exif_ifd)
    lat = struct.pack("<6I", 40, 1, 26, 1, 4630, 100)
    gps_ifd = _ifd([(0x0001, 2, 2, b"N\x00"), (0x0002, 5, 3, lat)], gps_off)
    # 回填指针
    ifd0 = bytearray(ifd0)
    for k in range(len(ifd0_entries)):
        e = 2 + 12 * k
        tag = struct.unpack_from("<H", ifd0, e)[0]
        if tag == 0x8769:
            struct.pack_into("<I", ifd0, e + 8, exif_off)
        if tag == 0x8825:
            struct.pack_into("<I", ifd0, e + 8, gps_off)
    tiff = b"II*\x00" + struct.pack("<I", 8) + bytes(ifd0) + exif_ifd + gps_ifd
    app1 = b"Exif\x00\x00" + tiff
    com = b"just a comment"
    sof = bytes([8]) + struct.pack(">HH", 30, 40) + bytes([3]) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    seg = lambda m, body: b"\xff" + bytes([m]) + struct.pack(">H", len(body) + 2) + body  # noqa: E731
    return b"\xff\xd8" + seg(0xE1, app1) + seg(0xFE, com) + seg(0xC0, sof) + b"\xff\xd9" + b"TRAILER" + FLAG.encode()


def make_png():
    def chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 2, 3, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", b"Comment\x00" + FLAG.encode())
            + chunk(b"zTXt", b"Author\x00\x00" + zlib.compress(b"bob the builder"))
            + chunk(b"iTXt", b"Description\x00\x00\x00en\x00\x00plain itxt")
            + chunk(b"IDAT", zlib.compress(b"\x00" * 30)) + chunk(b"IEND", b""))


def make_gif():
    return (b"GIF89a" + struct.pack("<HH", 5, 7) + b"\x00\x00\x00" + b"\x21\xfe"
            + bytes([len(FLAG)]) + FLAG.encode() + b"\x00" + b"\x3b")


def make_pdf():
    hx = ("﻿" + FLAG).encode("utf-16-be").hex()
    return (b"%PDF-1.4\n1 0 obj << /Title <" + hx.encode() + b"> /Author (Mallory \\(m\\)) >> endobj\n"
            b"<x:xmpmeta><dc:creator><rdf:Seq><rdf:li>Eve</rdf:li></rdf:Seq></dc:creator></x:xmpmeta>\n%%EOF\n")


class TestExiftool(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-exif-"))
        for name, data in (("p.jpg", make_jpeg()), ("p.png", make_png()), ("p.gif", make_gif()),
                           ("p.pdf", make_pdf()), ("p.txt", b"plain")):
            (self.tmp / name).write_bytes(data)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tags(self, *args):
        r = run("exiftool", *args, cwd=self.tmp)
        out = {}
        for line in r.stdout.decode().splitlines():
            if ": " in line:
                k, v = line.split(": ", 1)
                out[k.strip()] = v
        return r, out

    def test_jpeg_exif_comment_gps_trailer(self):
        r, t = self.tags("p.jpg")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FlagPilot shim", t["ExifTool Version Number"])     # 明说是替身
        self.assertEqual(t["File Type"], "JPEG")
        self.assertEqual(t["Image Description"], "secret notes")
        self.assertEqual(t["Artist"], "alice")
        self.assertEqual(t["XP Comment"], "XP " + FLAG)
        self.assertEqual(t["User Comment"], "hello from user comment")
        self.assertEqual(t["GPS Latitude"], "40 deg 26' 46.30\"")
        self.assertEqual(t["Exif 0xbeef"], "odd-field!")                     # 认不得的文本标签也不能漏
        self.assertEqual(t["Comment"], "just a comment")
        self.assertEqual((t["Image Width"], t["Image Height"]), ("40", "30"))
        self.assertIn("bytes after JPEG end marker", t["Trailer"])

    def test_png_gif_pdf_text(self):
        _, t = self.tags("p.png")
        self.assertEqual(t["Comment"], FLAG)
        self.assertEqual(t["Author"], "bob the builder")
        self.assertEqual(t["Description"], "plain itxt")
        self.assertEqual(t["Color Type"], "RGB with Alpha")
        _, t = self.tags("p.gif")
        self.assertEqual(t["Comment"], FLAG)
        _, t = self.tags("p.pdf")
        self.assertEqual(t["Title"], FLAG)
        self.assertEqual(t["Author"], "Mallory (m)")
        self.assertEqual(t["Creator"], "Eve")

    def test_options(self):
        r = run("exiftool", "-j", "p.png", cwd=self.tmp)
        self.assertEqual(json.loads(r.stdout)[0]["Comment"], FLAG)
        r = run("exiftool", "-b", "-Comment", "p.gif", cwd=self.tmp)
        self.assertEqual(r.stdout.decode(), FLAG)
        r = run("exiftool", "-s", "-s", "-s", "-Artist", "p.jpg", cwd=self.tmp)
        self.assertEqual(r.stdout.decode().strip(), "alice")
        _, t = self.tags("-s", "p.jpg")
        self.assertIn("ImageDescription", t)
        r = run("exiftool", "-Comment=x", "p.jpg", cwd=self.tmp)
        self.assertEqual(r.returncode, 2)                                     # 写操作明确拒绝
        r, t = self.tags("p.txt")
        self.assertIn("Unknown file type", t["Error"])
        r = run("exiftool", "nope.jpg", cwd=self.tmp)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
