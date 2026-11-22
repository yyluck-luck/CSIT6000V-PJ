"""容器后端的测试。

本机（以及多数课程节点）没有容器运行时，所以这里不跑真容器，只验证两件事：
1. 探测逻辑选对了 runtime、镜像不在本地时会回退而不是硬上；
2. 命令构造把网络、工作目录、资源限制都带上了。

真容器的行为要等 TACC 上跑一次才能确认——这一点在报告里必须写清楚。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flagpilot import sandbox  # noqa: E402
from flagpilot.config import SandboxLimits  # noqa: E402
from flagpilot.sandbox import Caps, Sandbox  # noqa: E402


def container_caps(runtime="docker", image="python:3.11-slim") -> Caps:
    return Caps(container=runtime, container_bin=f"/usr/bin/{runtime}",
                container_image=image, isolation="container")


class TestContainerArgv(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-container-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def sb(self, caps):
        return Sandbox(self.tmp / "work", limits=SandboxLimits(action_timeout_s=5), caps=caps)

    def test_docker_locks_down_network_and_limits(self):
        argv = self.sb(container_caps("docker"))._container_argv(["/bin/sh", "-c", "echo hi"])
        self.assertEqual(argv[0], "/usr/bin/docker")
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--rm", argv)
        self.assertIn("--memory", argv)
        self.assertIn("--pids-limit", argv)
        # 镜像之后的才是真正的命令，不能被参数解析吃掉
        self.assertEqual(argv[argv.index("python:3.11-slim") + 1:], ["/bin/sh", "-c", "echo hi"])

    def test_no_capabilities_no_privilege_gain_and_not_root(self):
        """2026-11-22, with the sandbox image (deploy/container): docker drops every capability, forbids setuid gains and
        runs as the host user; rootless podman keeps the host user's id instead (--user would lose the bind's write access)."""
        argv = self.sb(container_caps("docker"))._container_argv(["true"])
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")
        self.assertEqual(argv[argv.index("--security-opt") + 1], "no-new-privileges")
        if hasattr(os, "getuid"):
            self.assertEqual(argv[argv.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
        argv = self.sb(container_caps("podman"))._container_argv(["true"])
        self.assertEqual(argv[argv.index("--userns") + 1], "keep-id")
        self.assertNotIn("--user", argv)

    def test_the_sandbox_image_definitions_agree(self):
        """Dockerfile and flagpilot.def install the same lists, and those cover the tools doctor checks for."""
        d = Path(__file__).resolve().parent.parent / "deploy" / "container"
        for f in ("Dockerfile", "flagpilot.def"):
            text = (d / f).read_text(encoding="utf-8")
            self.assertIn("apt.txt", text)
            self.assertIn("pip.txt", text)
            self.assertIn("gem install --no-document zsteg", text)
            self.assertIn("python:3.12-slim-bookworm", text)
        apt = {ln.strip() for ln in (d / "apt.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")}
        pip = {ln.strip() for ln in (d / "pip.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")}
        for pkg in ("binwalk", "libimage-exiftool-perl", "tshark", "gdb", "sleuthkit", "foremost", "steghide", "binutils"):
            self.assertIn(pkg, apt)
        for mod in ("pycryptodome", "gmpy2", "z3-solver"):
            self.assertIn(mod, pip)

    def test_workdir_binds_at_same_absolute_path(self):
        """路径映射会让 solve.py 里的绝对路径失效，必须原路径 bind。"""
        sb = self.sb(container_caps("docker"))
        wd = str(sb.workdir)
        self.assertIn(f"{wd}:{wd}", sb._container_argv(["true"]))

    def test_apptainer_uses_containall(self):
        argv = self.sb(container_caps("apptainer", "docker://python:3.11-slim"))._container_argv(["true"])
        self.assertEqual(argv[0], "/usr/bin/apptainer")
        self.assertIn("exec", argv)
        self.assertIn("--containall", argv)
        self.assertIn("--no-home", argv)

    def test_no_container_means_no_wrapping(self):
        sb = self.sb(Caps(isolation="process"))
        self.assertFalse(sb.use_container)

    def test_use_container_false_overrides_detection(self):
        sb = Sandbox(self.tmp / "work2", caps=container_caps("docker"), use_container=False)
        self.assertFalse(sb.use_container)

    def test_host_rlimit_skipped_for_docker(self):
        """给 docker CLI 设 RLIMIT_CPU 会在 60s 后杀掉客户端，容器却还在跑。"""
        self.assertFalse(self.sb(container_caps("docker"))._host_rlimit())
        self.assertTrue(self.sb(container_caps("apptainer", "docker://x"))._host_rlimit())

    def test_container_kill_is_noop_without_container(self):
        self.sb(Caps(isolation="process"))._container_kill()  # 不抛异常即通过


class TestProbe(unittest.TestCase):
    def tearDown(self):
        sandbox._CAPS_CACHE = None

    def probe(self, which_map: dict, ready: bool):
        def fake_which(name):
            return which_map.get(name)

        ok = mock.Mock(returncode=0)
        with mock.patch.object(sandbox.shutil, "which", side_effect=fake_which), \
                mock.patch.object(sandbox, "_container_ready", return_value=ready), \
                mock.patch.object(sandbox.subprocess, "run", return_value=ok):
            return sandbox.probe_capabilities(force=True)

    def test_picks_apptainer_first(self):
        caps = self.probe({"apptainer": "/opt/apptainer", "docker": "/usr/bin/docker"}, True)
        self.assertEqual(caps.container, "apptainer")
        self.assertEqual(caps.isolation, "container")

    def test_falls_back_to_docker(self):
        caps = self.probe({"docker": "/usr/bin/docker"}, True)
        self.assertEqual(caps.container, "docker")

    def test_missing_image_keeps_process_isolation(self):
        """runtime 在但镜像不在本地：不能假装有容器，必须回退并说明原因。"""
        caps = self.probe({"docker": "/usr/bin/docker"}, False)
        self.assertEqual(caps.container, "none")
        self.assertEqual(caps.isolation, "process")
        self.assertTrue(any("镜像" in n for n in caps.notes), caps.notes)

    def test_no_runtime_at_all(self):
        caps = self.probe({}, False)
        self.assertEqual(caps.isolation, "process")
        self.assertTrue(any("无可用容器运行时" in n for n in caps.notes), caps.notes)


class TestImageConfig(unittest.TestCase):
    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"FLAGPILOT_CONTAINER_IMAGE": "/data/my.sif"}):
            self.assertEqual(sandbox._container_image("apptainer"), "/data/my.sif")

    def test_defaults_per_runtime(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLAGPILOT_CONTAINER_IMAGE", None)
            self.assertEqual(sandbox._container_image("docker"), "python:3.11-slim")
            self.assertTrue(sandbox._container_image("apptainer").startswith("docker://"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
