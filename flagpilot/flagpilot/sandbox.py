"""执行沙箱。

设计约束（对应 proposal 第 2 节 "Sandbox and verification contract"）：
  * 一次 attempt 一个工作目录，题目附件只读
  * 无网络：优先用 Linux network namespace（unshare），不可用时退化为
    "代理指向死地址 + 显式标注"，并且 trace 里如实记录隔离等级
  * 资源上限：CPU 秒、地址空间、单文件大小、进程数
  * 超时杀整个进程组，避免子进程残留

注意：本模块**不做**安全边界的承诺。它不是容器。在共享集群上，真正的
隔离靠 unshare/Apptainer；这里做的是"防手滑 + 防跑飞 + 留证据"。
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import SandboxLimits
from .tasks import Task, stage_assets

_MAX_CAPTURE = 256 * 1024  # 单个流最多回传 256KB 给 observation


def _ensure_dir(p: Path) -> None:
    """容忍并发创建同一个目录（批跑时多个线程会撞）。"""
    try:
        p.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass


@dataclass
class ExecResult:
    cmd: str
    ok: bool
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str
    truncated_stdout: bool = False
    truncated_stderr: bool = False

    @property
    def combined(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append("[stderr]\n" + self.stderr)
        return "\n".join(parts) if parts else "(no output)"


@dataclass
class Caps:
    """运行环境能力探测结果，写进 trace。"""

    net_isolation: str = "none"        # netns | proxy-only | none
    rlimit: bool = False
    unshare_bin: str | None = None
    python: str = "python3"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "net_isolation": self.net_isolation,
            "rlimit": self.rlimit,
            "python": self.python,
            "notes": list(self.notes),
        }


_CAPS_CACHE: Caps | None = None


def probe_capabilities(force: bool = False) -> Caps:
    """探测一次，进程内缓存。doctor 命令也会调用它。"""
    global _CAPS_CACHE
    if _CAPS_CACHE is not None and not force:
        return _CAPS_CACHE

    caps = Caps()
    py = shutil.which("python3") or shutil.which("python")
    if not py:
        # PATH 里找不到时退回当前解释器：平台常用 `python -m` 的绝对路径启动，
        # 此时 PATH 不含解释器目录，which 会落空，沙箱里 solve.py 就调不到 python3。
        py = sys.executable or "python3"
    caps.python = py

    try:
        import resource  # noqa: F401

        caps.rlimit = True
    except Exception:
        caps.notes.append("resource 模块不可用，资源上限未启用")
    if caps.rlimit and sys.platform == "darwin":
        # 写进 trace，别让报告里"有资源上限"这句话超出事实
        caps.notes.append("资源上限：CPU / FSIZE / NOFILE 生效；"
                          "RLIMIT_AS 与 RLIMIT_NPROC 在 macOS 不可用（已跳过）")

    unshare = shutil.which("unshare")
    caps.unshare_bin = unshare
    if unshare and os.name == "posix":
        for args in (["-rn", "true"], ["-n", "true"]):
            try:
                r = subprocess.run(
                    [unshare, *args],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                if r.returncode == 0:
                    caps.net_isolation = "netns"
                    caps.notes.append(f"网络隔离：unshare {' '.join(args)} 可用")
                    break
            except Exception:
                continue
    if caps.net_isolation == "none":
        caps.net_isolation = "proxy-only"
        caps.notes.append("网络隔离：无可用 namespace，退化为代理屏蔽（不可作为强隔离的证据）")

    _CAPS_CACHE = caps
    return caps


def _child_setup(limits: SandboxLimits):
    """返回 preexec_fn。必须吞掉所有异常，否则子进程起不来。

    每个 limit 单独 try：早先是一整块 try，结果 RLIMIT_AS 在 macOS 上抛
    ValueError（current limit exceeds maximum limit），后面的 FSIZE / NPROC /
    NOFILE 全被跳过了——实测 1MB 上限的沙箱能写出 5MB 文件。这类失败不会报错，
    只会让"有资源上限"这句话变成假的，所以必须逐项设置、逐项容错。
    """

    def _fn():  # pragma: no cover - 在子进程里执行
        try:
            import resource
        except Exception:
            return

        def _set(name: str, value: int) -> None:
            try:
                resource.setrlimit(getattr(resource, name), (value, value))
            except Exception:
                pass

        if limits.cpu_seconds:
            _set("RLIMIT_CPU", limits.cpu_seconds)
        # macOS：RLIMIT_AS 设不上（且设上了也不强制）；RLIMIT_NPROC 是"每用户"
        # 进程计数，用户本身早就超过 64/256，一设就让子进程 fork 直接 EAGAIN。
        # 这两个只在 Linux 上开，宁可少一层限制，也不能让工具全跑不起来。
        if sys.platform != "darwin":
            if limits.memory_mb:
                _set("RLIMIT_AS", limits.memory_mb * 1024 * 1024)
            if limits.max_procs:
                _set("RLIMIT_NPROC", limits.max_procs)
        if limits.file_size_mb:
            _set("RLIMIT_FSIZE", limits.file_size_mb * 1024 * 1024)
        _set("RLIMIT_NOFILE", 512)

    return _fn


class Sandbox:
    def __init__(
        self,
        workdir: Path,
        limits: SandboxLimits | None = None,
        isolate_network: bool = True,
        caps: Caps | None = None,
        tasks_root: Path | None = None,
    ):
        self.workdir = Path(workdir).resolve()
        self.tasks_root = tasks_root
        self.limits = limits or SandboxLimits()
        self.isolate_network = isolate_network
        self.caps = caps or probe_capabilities()
        _ensure_dir(self.workdir)
        _ensure_dir(self.workdir / ".tmp")

    # ---------- 准备 ----------

    def stage(self, task: Task) -> Path:
        """把题目附件放进 work/challenge，并设为只读。返回 challenge 目录。"""
        dest = self.workdir / "challenge"
        if dest.exists():
            self._rmtree_force(dest)
        n = stage_assets(task, dest, root=self.tasks_root)
        self._lock_readonly(dest)
        return dest

    @staticmethod
    def _lock_readonly(root: Path) -> None:
        for p in sorted(root.rglob("*"), key=lambda x: -len(str(x))):
            try:
                if p.is_file():
                    p.chmod(0o444)
                elif p.is_dir():
                    p.chmod(0o555)
            except Exception:
                pass
        try:
            root.chmod(0o555)
        except Exception:
            pass

    @staticmethod
    def _rmtree_force(p: Path) -> None:
        def _onerror(func, path, exc_info):
            try:
                os.chmod(path, 0o755)
                func(path)
            except Exception:
                pass

        shutil.rmtree(p, onerror=_onerror)

    def cleanup(self) -> None:
        for d in ("challenge",):
            p = self.workdir / d
            if p.exists():
                try:
                    p.chmod(0o755)
                except Exception:
                    pass
                self._rmtree_force(p)

    # ---------- 执行 ----------

    def run_shell(self, cmd: str, timeout: float | None = None) -> ExecResult:
        argv = ["/bin/sh", "-c", cmd] if os.name == "posix" else ["cmd.exe", "/c", cmd]
        return self._exec(argv, display=cmd, timeout=timeout)

    def run_python(self, code: str, timeout: float | None = None) -> ExecResult:
        path = self.workdir / ".tmp" / "action.py"
        _ensure_dir(path.parent)
        path.write_text(code, encoding="utf-8")
        return self._exec([self.caps.python, str(path)], display="python3 action.py", timeout=timeout)

    def _exec(self, argv: list[str], display: str, timeout: float | None = None) -> ExecResult:
        timeout = timeout if timeout is not None else self.limits.action_timeout_s
        # 沙箱里 `python3` 必须可解析：模型写的 solve.py 常用 `python3 x.py`
        # 或 `subprocess.run("python3 ...", shell=True)`。宿主若是 conda / venv，
        # 解释器可能只以绝对路径存在（main.py 用 sys.executable 启动时尤其如此），
        # 此时 PATH 里没有它的目录，重放就会报 `/bin/sh: python3: not found`。
        # 把解释器所在目录提到 PATH 最前，保证沙箱用的就是 agent 用的那个。
        base_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        py_dir = os.path.dirname(str(self.caps.python))
        if py_dir and py_dir not in base_path.split(os.pathsep):
            base_path = py_dir + os.pathsep + base_path
        env = {
            "PATH": base_path,
            "HOME": str(self.workdir),
            "TMPDIR": str(self.workdir / ".tmp"),
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if self.caps.net_isolation != "netns":
            # 软屏蔽：让出网请求直接失败，而不是挂住等超时
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                env[k] = "http://127.0.0.1:9"
            env["NO_PROXY"] = ""

        real_argv = argv
        if self.isolate_network and self.caps.net_isolation == "netns" and self.caps.unshare_bin:
            real_argv = [self.caps.unshare_bin, "-rn", "--", *argv]

        t0 = time.monotonic()
        # 输出落到工作区内的固定文件，不用 tempfile：
        #   ① TemporaryFile 在 POSIX 上创建后立刻 unlink，某些安全策略会
        #      拦掉这个删除（实测过：连 tempfile.gettempdir() 都会因此抛错），
        #      于是每次动作都 spawn failed；
        #   ② 固定文件名可以下一轮直接截断重写，不产生垃圾，也不占 FD；
        #   ③ 落盘位置可查（work/.tmp/），出事能直接看。
        cap_dir = self.workdir / ".tmp"
        _ensure_dir(cap_dir)
        out_path = cap_dir / "last_stdout.bin"
        err_path = cap_dir / "last_stderr.bin"
        try:
            out_f = open(out_path, "wb")
            err_f = open(err_path, "wb")
        except OSError as e:
            return ExecResult(display, False, None, False,
                              int((time.monotonic() - t0) * 1000), "",
                              f"spawn failed: 无法创建捕获文件 {e}")
        try:
            try:
                proc = subprocess.Popen(
                    real_argv,
                    cwd=str(self.workdir),
                    stdin=subprocess.DEVNULL,
                    stdout=out_f,
                    stderr=err_f,
                    env=env,
                    start_new_session=True,
                    preexec_fn=_child_setup(self.limits) if os.name == "posix" else None,
                )
            except Exception as e:
                return ExecResult(display, False, None, False, int((time.monotonic() - t0) * 1000), "", f"spawn failed: {e}")

            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_group(proc)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
        finally:
            out_f.close()
            err_f.close()

        try:
            with open(out_path, "rb") as fh:
                out_b = fh.read(_MAX_CAPTURE + 1)
            with open(err_path, "rb") as fh:
                err_b = fh.read(_MAX_CAPTURE + 1)
        except OSError:
            out_b, err_b = b"", b""

        dur = int((time.monotonic() - t0) * 1000)
        return ExecResult(
            cmd=display,
            ok=(proc.returncode == 0 and not timed_out),
            exit_code=proc.returncode,
            timed_out=timed_out,
            duration_ms=dur,
            stdout=out_b[:_MAX_CAPTURE].decode("utf-8", "replace"),
            stderr=err_b[:_MAX_CAPTURE].decode("utf-8", "replace"),
            truncated_stdout=len(out_b) > _MAX_CAPTURE,
            truncated_stderr=len(err_b) > _MAX_CAPTURE,
        )

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
