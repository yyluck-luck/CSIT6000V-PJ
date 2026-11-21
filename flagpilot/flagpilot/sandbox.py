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
    # 给模型的一句可操作提示（不是命令输出）。目前只有提交前自检用它，
    # 单独一个字段是为了不把提示混进 stdout——那会污染"脚本到底打印了什么"。
    note: str = ""

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
    # 容器后端。proposal 要的是 "one container per attempt"，没有容器时
    # 退化为进程级，并把最终等级如实写进 trace——这一项不能含糊，
    # 报告里"有没有容器隔离"全靠它。
    container: str = "none"            # apptainer | singularity | docker | podman | none
    container_bin: str | None = None
    container_image: str = ""
    isolation: str = "process"         # container | netns | process
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "net_isolation": self.net_isolation,
            "rlimit": self.rlimit,
            "python": self.python,
            "container": self.container,
            "container_image": self.container_image,
            "isolation": self.isolation,
            "notes": list(self.notes),
        }


_CAPS_CACHE: Caps | None = None

# 容器镜像。proposal 要 "one container per attempt"，但本机和课程节点都不一定
# 有 runtime，更不一定有镜像。这里只探测、不自动拉取——自动 pull 在集群上
# 会变成"一个作业卡在 docker pull 直到墙钟耗尽"，比没有容器更糟。
_DEFAULT_IMAGES = {
    "apptainer": "docker://python:3.11-slim",
    "singularity": "docker://python:3.11-slim",
    "docker": "python:3.11-slim",
    "podman": "python:3.11-slim",
}


def _container_image(runtime: str) -> str:
    return os.environ.get("FLAGPILOT_CONTAINER_IMAGE") or _DEFAULT_IMAGES.get(runtime, "")


def _container_ready(runtime: str, binary: str, image: str) -> bool:
    """镜像在不在本地。只查不拉，探测阶段拉镜像会把启动时间拖成分钟级。"""
    if not image:
        return False
    try:
        if runtime in ("apptainer", "singularity"):
            if not image.startswith("docker://"):
                return Path(image).expanduser().exists()
            r = subprocess.run([binary, "cache", "list"], capture_output=True,
                               timeout=15, text=True)
            return image.split("docker://", 1)[1] in (r.stdout or "")
        r = subprocess.run([binary, "image", "inspect", image],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _probe_container(caps: Caps) -> None:
    """按 apptainer → singularity → docker → podman 找可用的容器运行时。

    这个顺序是照着实际部署来的：TACC 上只有 apptainer，共享集群一般不给
    docker 权限，podman 是最后的选择。全都不可用就保持进程级，由调用方
    把真实等级写进 trace——不能含糊成"有隔离"。
    """
    for runtime in ("apptainer", "singularity", "docker", "podman"):
        binary = shutil.which(runtime)
        if not binary:
            continue
        try:
            v = subprocess.run([binary, "--version"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10)
            if v.returncode != 0:
                continue
        except Exception:
            continue
        image = _container_image(runtime)
        if not _container_ready(runtime, binary, image):
            caps.notes.append(
                f"容器：{runtime} 可用，但镜像 {image or '(未配置)'} 不在本地；"
                f"设 FLAGPILOT_CONTAINER_IMAGE 或先拉取镜像")
            continue
        caps.container = runtime
        caps.container_bin = binary
        caps.container_image = image
        caps.isolation = "container"
        caps.notes.append(f"隔离：{runtime} 容器，每 attempt 一个（镜像 {image}）")
        return
    caps.notes.append("隔离：无可用容器运行时，退化为进程级 + rlimit（非容器）")


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

    # 容器优先于 namespace：proposal 的承诺是 "one container per attempt"，
    # namespace 只是它的 fallback。容器模式下网络由 runtime 的 --network none
    # 管，不再叠加 unshare——套两层只会让超时杀进程更难收干净。
    _probe_container(caps)
    if caps.isolation != "container":
        caps.isolation = "netns" if caps.net_isolation == "netns" else "process"

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
        use_container: bool = True,
    ):
        self.workdir = Path(workdir).resolve()
        self.tasks_root = tasks_root
        self.limits = limits or SandboxLimits()
        self.isolate_network = isolate_network
        self.caps = caps or probe_capabilities()
        # 只有探测确认了容器 + 本地镜像，才真的走容器。否则保持进程级，
        # 免得每条动作都先失败一次再回退。
        self.use_container = bool(use_container and self.caps.isolation == "container")
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

    def _container_name(self) -> str:
        """容器名要可预测，超时时靠它做 rm -f 收尸。"""
        return f"flagpilot-{os.getpid()}-{abs(hash(str(self.workdir))) % 10 ** 8}"

    def _container_argv(self, argv: list[str]) -> list[str]:
        """把宿主命令包成一次容器执行。

        工作目录按**原绝对路径** bind 进去，不做路径映射：沙箱里的 solve.py
        常以绝对路径引用自己（run_python 写的就是 workdir/.tmp/action.py），
        换成别的挂载点会让这些路径全部失效。
        """
        wd = str(self.workdir)
        binary, image = self.caps.container_bin, self.caps.container_image
        if self.caps.container in ("apptainer", "singularity"):
            return [binary, "exec", "--containall", "--no-home",
                    "--bind", f"{wd}:{wd}", "--pwd", wd, image, *argv]
        cmd = [binary, "run", "--rm", "-i", "--network", "none",
               "-v", f"{wd}:{wd}", "-w", wd, "--name", self._container_name()]
        if self.limits.memory_mb:
            cmd += ["--memory", f"{self.limits.memory_mb}m"]
        if self.limits.max_procs:
            cmd += ["--pids-limit", str(self.limits.max_procs)]
        return [*cmd, image, *argv]

    def _container_kill(self) -> None:
        """docker run 被 SIGKILL 后容器本身还活着，必须显式 rm -f。

        apptainer 不需要：它没有常驻 daemon，exec 结束进程就没了。
        """
        if not self.use_container or self.caps.container in ("apptainer", "singularity"):
            return
        try:
            subprocess.run([self.caps.container_bin, "rm", "-f", self._container_name()],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except Exception:
            pass

    @property
    def python_cmd(self) -> str:
        """沙箱里能用的解释器。

        容器模式下必须用裸名 `python3`：宿主的绝对路径（/usr/bin/python3、
        conda 的 bin/python3）在容器里根本不存在，直接拼进去会变成
        "no such file or directory"。进程级模式才用探测到的绝对路径。
        """
        return "python3" if self.use_container else self.caps.python

    def run_shell(self, cmd: str, timeout: float | None = None) -> ExecResult:
        argv = ["/bin/sh", "-c", cmd] if os.name == "posix" else ["cmd.exe", "/c", cmd]
        return self._exec(argv, display=cmd, timeout=timeout)

    def run_python(self, code: str, timeout: float | None = None) -> ExecResult:
        path = self.workdir / ".tmp" / "action.py"
        _ensure_dir(path.parent)
        path.write_text(code, encoding="utf-8")
        return self._exec([self.python_cmd, str(path)], display="python3 action.py",
                          timeout=timeout)

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
        # 部署方额外提供的纯 Python 库（TACC 课程运行时没有 pycryptodome / gmpy2，
        # deploy/tcloud 在提交时把 wheel 解进作业目录）。环境是白名单，不透传宿主的 PYTHONPATH；
        # 只认这一个显式变量，agent 动作与重放用的是同一个 _exec，两边看到的库一致。
        extra_py = os.environ.get("FLAGPILOT_SANDBOX_PYTHONPATH", "").strip()
        if extra_py:
            env["PYTHONPATH"] = extra_py
        if self.caps.net_isolation != "netns":
            # 软屏蔽：让出网请求直接失败，而不是挂住等超时
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                env[k] = "http://127.0.0.1:9"
            env["NO_PROXY"] = ""

        real_argv = argv
        if self.use_container:
            real_argv = self._container_argv(argv)
        elif self.isolate_network and self.caps.net_isolation == "netns" and self.caps.unshare_bin:
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
                    preexec_fn=(_child_setup(self.limits)
                                if os.name == "posix" and self._host_rlimit() else None),
                )
            except Exception as e:
                return ExecResult(display, False, None, False, int((time.monotonic() - t0) * 1000), "", f"spawn failed: {e}")

            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_group(proc)
                self._container_kill()
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

    def _host_rlimit(self) -> bool:
        """容器模式下要不要给宿主进程设 rlimit。

        docker/podman 的 CLI 是宿主进程：给它设 RLIMIT_CPU，60 秒后被杀的是
        docker 客户端，容器里的任务还在跑——限制该交给 --memory / --pids-limit。
        apptainer 是 fork+exec 进容器，rlimit 会被容器内进程继承，照旧设。
        """
        return not (self.use_container and self.caps.container in ("docker", "podman"))

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
