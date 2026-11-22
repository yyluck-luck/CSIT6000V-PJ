"""Web 层的三个提速件：gzip 协商、静态文件缓存、短 TTL 缓存。

它们全都"只在某些请求头下才生效"，出问题时不会报错、只会表现成"页面还是旧的"
或者"每次刷新都重传 80KB"。所以按协商规则逐条钉住，比端到端测更容易定位。
"""

import gzip
import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import httpcache


class TestAcceptsGzip(unittest.TestCase):
    def test_plain_gzip(self):
        self.assertTrue(httpcache.accepts_gzip("gzip"))

    def test_case_and_spacing_do_not_matter(self):
        self.assertTrue(httpcache.accepts_gzip("deflate, GZIP"))
        self.assertTrue(httpcache.accepts_gzip("  gzip  "))

    def test_quality_zero_is_a_refusal(self):
        self.assertFalse(httpcache.accepts_gzip("gzip;q=0"))
        self.assertFalse(httpcache.accepts_gzip("gzip;q=0.0"))
        self.assertFalse(httpcache.accepts_gzip("gzip; q=0.000"))

    def test_a_small_quality_still_accepts(self):
        self.assertTrue(httpcache.accepts_gzip("gzip;q=0.1"))

    def test_star_means_everything(self):
        self.assertTrue(httpcache.accepts_gzip("*"))

    def test_star_refused_but_gzip_listed_still_accepts(self):
        self.assertTrue(httpcache.accepts_gzip("*;q=0, gzip"))

    def test_source_order_does_not_decide(self):
        self.assertTrue(httpcache.accepts_gzip("gzip;q=0.2, *;q=0.9"))

    def test_something_else_means_no(self):
        self.assertFalse(httpcache.accepts_gzip("deflate, br"))
        self.assertFalse(httpcache.accepts_gzip("x-gzip"))

    def test_missing_header_means_no(self):
        self.assertFalse(httpcache.accepts_gzip(None))
        self.assertFalse(httpcache.accepts_gzip(""))

    def test_a_broken_quality_is_treated_as_refused(self):
        self.assertFalse(httpcache.accepts_gzip("gzip;q=lots"))


class TestMaybeGzip(unittest.TestCase):
    def test_small_bodies_are_not_compressed(self):
        body = b"x" * (httpcache.GZIP_MIN_BYTES - 1)
        out, gz = httpcache.maybe_gzip(body, "gzip")
        self.assertFalse(gz)
        self.assertEqual(out, body)

    def test_large_bodies_are_compressed_when_accepted(self):
        body = b"hello " * 1000
        out, gz = httpcache.maybe_gzip(body, "gzip")
        self.assertTrue(gz)
        self.assertLess(len(out), len(body))
        self.assertEqual(gzip.decompress(out), body)

    def test_large_bodies_stay_plain_without_accept(self):
        body = b"hello " * 1000
        out, gz = httpcache.maybe_gzip(body, None)
        self.assertFalse(gz)
        self.assertEqual(out, body)

    def test_the_threshold_itself_is_compressed(self):
        body = b"x" * httpcache.GZIP_MIN_BYTES
        self.assertTrue(httpcache.maybe_gzip(body, "gzip")[1])


class TestStaticFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-cache-"))
        self.path = self.tmp / "index.html"
        self.path.write_bytes(b"<html>one</html>")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_fills_raw_gz_and_etag(self):
        f = httpcache.StaticFile(self.path).load()
        self.assertEqual(f.raw, b"<html>one</html>")
        self.assertEqual(gzip.decompress(f.gz), f.raw)
        self.assertTrue(f.etag.startswith('"') and f.etag.endswith('"'))

    def test_the_etag_is_stable_for_the_same_bytes(self):
        a = httpcache.StaticFile(self.path).load().etag
        b = httpcache.StaticFile(self.path).load().etag
        self.assertEqual(a, b)
        self.assertEqual(a, '"' + hashlib.sha1(b"<html>one</html>").hexdigest()[:20] + '"')

    def test_editing_the_file_changes_the_etag(self):
        f = httpcache.StaticFile(self.path).load()
        before = f.etag
        self.path.write_bytes(b"<html>two, a bit longer</html>")
        st = self.path.stat()
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        self.assertNotEqual(f.load().etag, before)
        self.assertEqual(f.raw, b"<html>two, a bit longer</html>")

    def test_an_unchanged_file_is_not_read_again(self):
        f = httpcache.StaticFile(self.path).load()
        calls = []
        real = Path.read_bytes

        def counting(p):
            calls.append(p)
            return real(p)

        with mock.patch.object(Path, "read_bytes", counting):
            f.load()
        self.assertEqual(calls, [], "文件没改却重读了一遍")

    def test_a_missing_file_raises_and_the_caller_can_fall_back(self):
        f = httpcache.StaticFile(self.tmp / "nope.html")
        with self.assertRaises(OSError):
            f.load()


class TestTTLCache(unittest.TestCase):
    def test_the_value_is_reused_within_the_ttl(self):
        c = httpcache.TTLCache(ttl_s=60)
        calls = []
        first = c.get("k", lambda: calls.append(1) or "v")
        second = c.get("k", lambda: calls.append(1) or "v2")
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_another_key_recomputes_immediately(self):
        c = httpcache.TTLCache(ttl_s=60)
        self.assertEqual(c.get("a", lambda: 1), 1)
        self.assertEqual(c.get("b", lambda: 2), 2)
        self.assertEqual(c.get("a", lambda: 3), 3)

    def test_the_entry_expires(self):
        c = httpcache.TTLCache(ttl_s=10)
        with mock.patch.object(time, "monotonic", return_value=100.0):
            self.assertEqual(c.get("k", lambda: 1), 1)
        with mock.patch.object(time, "monotonic", return_value=111.0):
            self.assertEqual(c.get("k", lambda: 2), 2)

    def test_a_failing_compute_is_not_cached(self):
        c = httpcache.TTLCache(ttl_s=60)

        def boom():
            raise RuntimeError("provider down")

        with self.assertRaises(RuntimeError):
            c.get("k", boom)
        self.assertEqual(c.get("k", lambda: "ok"), "ok")

    def test_clear_forgets_everything(self):
        c = httpcache.TTLCache(ttl_s=60)
        c.get("k", lambda: 1)
        c.clear()
        self.assertEqual(c.get("k", lambda: 2), 2)


class TestSecretTag(unittest.TestCase):
    def test_empty_stays_empty(self):
        self.assertEqual(httpcache.secret_tag(""), "")

    def test_the_tag_is_short_and_not_the_secret(self):
        tag = httpcache.secret_tag("sk-live-abcdef")
        self.assertEqual(len(tag), 12)
        self.assertNotIn("sk-live", tag)

    def test_the_same_secret_gives_the_same_tag(self):
        self.assertEqual(httpcache.secret_tag("k1"), httpcache.secret_tag("k1"))
        self.assertNotEqual(httpcache.secret_tag("k1"), httpcache.secret_tag("k2"))


if __name__ == "__main__":
    unittest.main()
