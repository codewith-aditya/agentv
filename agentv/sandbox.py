from __future__ import annotations

import asyncio
import io
import logging
import os
import posixpath
import re
import shlex
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    backend: str = ""

    def combined(self, limit: int = 20000) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout.rstrip())
        if self.stderr:
            parts.append("[stderr]\n" + self.stderr.rstrip())
        text = "\n".join(parts).strip() or "(no output)"
        if self.timed_out:
            text += f"\n[timeout after {self.duration:.0f}s — process killed]"
        if self.exit_code not in (0, 124, 137):
            text += f"\n[exit code {self.exit_code}]"
        if len(text) > limit:
            text = text[:limit] + f"\n…[truncated {len(text) - limit} chars]"
        return text


def safe_chat_id(chat_id: int | str) -> str:
    return re.sub(r"[^0-9a-zA-Z_-]", "_", str(chat_id))


class BaseSandbox:
    backend = "base"

    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self._sem = asyncio.Semaphore(max(1, cfg.sandbox_max_concurrent))

    async def start(self) -> None:  # pragma: no cover - overridden
        pass

    async def close(self) -> None:  # pragma: no cover - overridden
        pass

    async def exec(
        self,
        chat_id: int | str,
        command: str,
        timeout: int | None = None,
        network: bool = True,
        env: dict[str, str] | None = None,
    ) -> ExecResult:  # pragma: no cover - overridden
        raise NotImplementedError

    async def exec_user(
        self,
        chat_id: int | str,
        command: str,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """Per-user isolated shell (used for non-owner users)."""
        return await self.exec(chat_id, command, timeout=timeout, network=False, env=env)

    async def write_file(self, chat_id: int | str, relpath: str, data: bytes) -> str:  # pragma: no cover
        raise NotImplementedError

    async def read_file(self, chat_id: int | str, relpath: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def export_path(self, chat_id: int | str, relpath: str) -> Path:  # pragma: no cover
        raise NotImplementedError

    def health(self) -> dict[str, object]:
        return {"backend": self.backend, "ok": True}


class LocalSandbox(BaseSandbox):
    """Development fallback: runs commands on the host in a per-chat directory.

    Not a security boundary — production deployments must use DockerSandbox.
    """

    backend = "local"

    def __init__(self, cfg: Settings):
        super().__init__(cfg)
        self.root = cfg.data_path / "workspace"
        self.root.mkdir(parents=True, exist_ok=True)

    def _chat_dir(self, chat_id: int | str) -> Path:
        p = self.root / "chats" / safe_chat_id(chat_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def exec(
        self,
        chat_id: int | str,
        command: str,
        timeout: int | None = None,
        network: bool = True,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        timeout = timeout or self.cfg.sandbox_timeout
        cwd = self._chat_dir(chat_id)
        full_env = os.environ.copy()
        full_env.update({"HOME": str(cwd), "TERM": "dumb", "NO_COLOR": "1"})
        full_env.update(env or {})
        started = time.monotonic()
        async with self._sem:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=full_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                duration = time.monotonic() - started
                return ExecResult(
                    exit_code=proc.returncode or 0,
                    stdout=out.decode("utf-8", "replace"),
                    stderr=err.decode("utf-8", "replace"),
                    duration=duration,
                    backend=self.backend,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ExecResult(
                    exit_code=124,
                    stdout="",
                    stderr="",
                    duration=time.monotonic() - started,
                    timed_out=True,
                    backend=self.backend,
                )

    def _resolve(self, chat_id: int | str, relpath: str) -> Path:
        base = self._chat_dir(chat_id)
        target = (base / relpath).resolve()
        if not str(target).startswith(str(base.resolve())):
            raise ValueError("path escapes the chat workspace")
        return target

    async def write_file(self, chat_id: int | str, relpath: str, data: bytes) -> str:
        target = self._resolve(chat_id, relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)

    async def read_file(self, chat_id: int | str, relpath: str) -> bytes:
        return self._resolve(chat_id, relpath).read_bytes()

    def export_path(self, chat_id: int | str, relpath: str) -> Path:
        return self._resolve(chat_id, relpath)

    def health(self) -> dict[str, object]:
        return {"backend": self.backend, "ok": True, "root": str(self.root)}


class DockerSandbox(BaseSandbox):
    """Long-lived hardened runner containers, one with network and one offline."""

    backend = "docker"

    @staticmethod
    def _size_bytes(value: str) -> int:
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*", (value or "").lower())
        if not match:
            return 0
        number = float(match.group(1))
        unit = match.group(2)
        factor = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}[unit]
        return int(number * factor)

    async def _workspace_used(self, container) -> int:
        result = await asyncio.to_thread(
            container.exec_run,
            ["bash", "-lc", "du -sb /workspace 2>/dev/null | cut -f1"],
            user="0:0",
            demux=True,
        )
        out = result.output[0] if isinstance(result.output, tuple) else result.output
        try:
            return int((out or b"0").decode().strip() or 0)
        except ValueError:
            return 0

    async def _quota_block(self, container) -> ExecResult | None:
        limit = self._size_bytes(self.cfg.sandbox_storage)
        if limit <= 0:
            return None
        used = await self._workspace_used(container)
        if used <= limit:
            return None
        return ExecResult(
            exit_code=1,
            stdout="",
            stderr=(
                f"storage quota exceeded: {used / 1024**3:.2f} GB used of "
                f"{limit / 1024**3:.1f} GB — delete files in the workspace first."
            ),
            duration=0.0,
            backend=self.backend,
        )

    def __init__(self, cfg: Settings):
        super().__init__(cfg)
        self._client = None
        self._containers: dict[bool, object] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        import docker  # imported lazily so dev mode works without the SDK

        self._client = await asyncio.to_thread(docker.from_env)
        try:
            await asyncio.to_thread(self._client.ping)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"docker daemon unreachable: {exc}") from exc
        await self._ensure_image()
        await self._ensure_container(network=True)

    async def _ensure_image(self) -> None:
        image = self.cfg.sandbox_image
        try:
            await asyncio.to_thread(self._client.images.get, image)
            return
        except Exception:  # noqa: BLE001
            log.info("sandbox image %s missing — building from %s", image, self.cfg.runner_dir)
        context = self.cfg.runner_dir
        if not context.exists():
            raise RuntimeError(f"runner build context missing: {context}")
        await asyncio.to_thread(
            self._client.images.build,
            path=str(context),
            tag=image,
            rm=True,
            pull=False,
        )
        log.info("sandbox image %s built", image)

    async def _ensure_container(self, network: bool):
        async with self._lock:
            existing = self._containers.get(network)
            if existing is not None:
                await asyncio.to_thread(existing.reload)
                if existing.status != "running":
                    await asyncio.to_thread(existing.start)
                return existing
            name = "agentv-run-net" if network else "agentv-run-offline"
            import docker as docker_mod

            try:
                container = await asyncio.to_thread(self._client.containers.get, name)
                await asyncio.to_thread(container.reload)
                if container.status != "running":
                    await asyncio.to_thread(container.start)
            except docker_mod.errors.NotFound:
                kwargs = dict(
                    image=self.cfg.sandbox_image,
                    command=["sleep", "infinity"],
                    name=name,
                    detach=True,
                    auto_remove=False,
                    labels={"agentv": "1", "agentv.network": "1" if network else "0"},
                    working_dir=self.cfg.workspace_dir,
                    volumes={self.cfg.sandbox_volume: {"bind": self.cfg.workspace_dir, "mode": "rw"}},
                    environment={
                        "HOME": "/home/sandbox",
                        "TERM": "dumb",
                        "NO_COLOR": "1",
                        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                    },
                    mem_limit=self.cfg.sandbox_memory,
                    storage_opt={"size": self.cfg.sandbox_storage},
                    nano_cpus=int(self.cfg.sandbox_cpus * 1_000_000_000),
                    pids_limit=self.cfg.sandbox_pids,
                    cap_drop=["ALL"],
                    # the workspace volume is populated as root via the Docker API,
                    # so the container needs to be able to chown it back to uid 1000
                    cap_add=["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID"],
                    security_opt=["no-new-privileges:true"],
                    network_mode="bridge" if network else "none",
                    init=True,
                )
                container = await asyncio.to_thread(self._client.containers.run, **kwargs)
            self._containers[network] = container
            return container

    async def close(self) -> None:
        # containers are intentionally left running for fast restarts
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ exec
    async def exec(
        self,
        chat_id: int | str,
        command: str,
        timeout: int | None = None,
        network: bool = True,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        timeout = timeout or self.cfg.sandbox_timeout
        use_net = bool(network and self.cfg.sandbox_network)
        container = await self._ensure_container(use_net)
        blocked = await self._quota_block(container)
        if blocked is not None:
            return blocked
        chat_dir = posixpath.join(self.cfg.workspace_dir, "chats", safe_chat_id(chat_id))
        await self._run_root(container, ["mkdir", "-p", chat_dir])
        await self._run_root(container, ["chown", "-R", "1000:1000", chat_dir])

        wrapped = f"timeout -s KILL {int(timeout)}s bash -lc {shlex.quote(command)}"
        started = time.monotonic()
        async with self._sem:
            result = await asyncio.to_thread(
                container.exec_run,
                ["bash", "-lc", wrapped],
                workdir=chat_dir,
                environment=env or {},
                demux=True,
                user="1000:1000",
            )
        duration = time.monotonic() - started
        out_b, err_b = (result.output or (b"", b"")) if isinstance(result.output, tuple) else (result.output or b"", b"")
        return ExecResult(
            exit_code=int(result.exit_code or 0),
            stdout=(out_b or b"").decode("utf-8", "replace"),
            stderr=(err_b or b"").decode("utf-8", "replace"),
            duration=duration,
            timed_out=int(result.exit_code or 0) in (124, 137),
            backend=self.backend,
        )

    async def _run_root(self, container, argv: list[str]) -> None:
        await asyncio.to_thread(container.exec_run, argv, user="0:0", demux=True)

    # ------------------------------------------------------------------ per-user shell
    def _user_host_dir(self, ws_key: str) -> Path:
        # Always resolve through the Docker volume: the bind mount source is a
        # HOST path, not this container's /workspace path.
        volume = self._client.volumes.get(self.cfg.sandbox_volume)
        base = Path(volume.attrs["Mountpoint"]) / "chats" / safe_chat_id(ws_key)
        base.mkdir(parents=True, exist_ok=True)
        try:
            os.chown(base, 1000, 1000)
        except OSError:
            pass
        return base

    async def _ensure_user_container(self, ws_key: str):
        import docker as docker_mod

        host_dir = self._user_host_dir(ws_key)
        name = f"agentv-user-{safe_chat_id(ws_key)}"[:60]
        try:
            container = await asyncio.to_thread(self._client.containers.get, name)
            await asyncio.to_thread(container.reload)
            if container.status != "running":
                await asyncio.to_thread(container.start)
            return container
        except docker_mod.errors.NotFound:
            kwargs = dict(
                image=self.cfg.sandbox_image,
                command=["sleep", "infinity"],
                name=name,
                detach=True,
                auto_remove=False,
                labels={"agentv": "1", "agentv.user": safe_chat_id(ws_key)},
                working_dir="/workspace",
                volumes={str(host_dir): {"bind": "/workspace", "mode": "rw"}},
                environment={"HOME": "/home/sandbox", "TERM": "dumb", "NO_COLOR": "1"},
                mem_limit=self.cfg.sandbox_memory,
                nano_cpus=int(self.cfg.sandbox_cpus * 1_000_000_000),
                pids_limit=self.cfg.sandbox_pids,
                cap_drop=["ALL"],
                cap_add=["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID"],
                security_opt=["no-new-privileges:true"],
                network_mode="none",
                init=True,
            )
            return await asyncio.to_thread(self._client.containers.run, **kwargs)

    async def exec_user(
        self,
        chat_id: int | str,
        command: str,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        timeout = timeout or self.cfg.sandbox_timeout
        container = await self._ensure_user_container(safe_chat_id(chat_id))
        wrapped = f"timeout -s KILL {int(timeout)}s bash -lc {shlex.quote(command)}"
        started = time.monotonic()
        async with self._sem:
            result = await asyncio.to_thread(
                container.exec_run,
                ["bash", "-lc", wrapped],
                workdir="/workspace",
                environment=env or {},
                demux=True,
                user="1000:1000",
            )
        duration = time.monotonic() - started
        out_b, err_b = (result.output or (b"", b"")) if isinstance(result.output, tuple) else (result.output or b"", b"")
        return ExecResult(
            exit_code=int(result.exit_code or 0),
            stdout=(out_b or b"").decode("utf-8", "replace"),
            stderr=(err_b or b"").decode("utf-8", "replace"),
            duration=duration,
            timed_out=int(result.exit_code or 0) in (124, 137),
            backend=self.backend,
        )

    # ------------------------------------------------------------------ files
    async def write_file(self, chat_id: int | str, relpath: str, data: bytes) -> str:
        container = await self._ensure_container(True)
        blocked = await self._quota_block(container)
        if blocked is not None:
            raise RuntimeError(blocked.stderr or "storage quota exceeded")
        chat_dir = posixpath.join(self.cfg.workspace_dir, "chats", safe_chat_id(chat_id))
        target = posixpath.normpath(posixpath.join(chat_dir, relpath))
        if not target.startswith(chat_dir + "/"):
            raise ValueError("path escapes the chat workspace")
        folder, name = posixpath.split(target)
        await self._run_root(container, ["mkdir", "-p", folder])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        await asyncio.to_thread(container.put_archive, folder, buf.getvalue())
        await self._run_root(container, ["chown", "-R", "1000:1000", folder])
        return posixpath.join(chat_dir, relpath)

    async def _get_archive(self, container, path: str) -> bytes:
        chunks, _stat = await asyncio.to_thread(container.get_archive, path)
        raw = b"".join(chunks)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            member = next((m for m in tar.getmembers() if m.isfile()), None)
            if member is None:
                raise FileNotFoundError(path)
            fh = tar.extractfile(member)
            return fh.read() if fh else b""

    async def read_file(self, chat_id: int | str, relpath: str) -> bytes:
        container = await self._ensure_container(True)
        chat_dir = posixpath.join(self.cfg.workspace_dir, "chats", safe_chat_id(chat_id))
        target = posixpath.normpath(posixpath.join(chat_dir, relpath))
        if not target.startswith(chat_dir + "/"):
            raise ValueError("path escapes the chat workspace")
        return await self._get_archive(container, target)

    def export_path(self, chat_id: int | str, relpath: str) -> Path:
        raise RuntimeError("use export_file() for docker sandboxes")

    async def export_file(self, chat_id: int | str, relpath: str) -> Path:
        # Fast path: the bot mounts the same named volume at WORKSPACE_DIR.
        local_root = Path(self.cfg.workspace_dir)
        if local_root.is_dir():
            candidate = (local_root / "chats" / safe_chat_id(chat_id) / relpath).resolve()
            if candidate.is_file():
                return candidate
        container = await self._ensure_container(True)
        chat_dir = posixpath.join(self.cfg.workspace_dir, "chats", safe_chat_id(chat_id))
        target = posixpath.normpath(posixpath.join(chat_dir, relpath))
        data = await self._get_archive(container, target)
        dest = self.cfg.exports_path / safe_chat_id(chat_id) / Path(relpath).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    # ------------------------------------------------------------------ helpers
    async def container_exec_prefix(self) -> list[str] | None:
        """Prefix MCP stdio servers so they also run inside the sandbox."""
        container = await self._ensure_container(True)
        return ["docker", "exec", "-i", container.name]

    async def container_name(self) -> str | None:
        container = await self._ensure_container(True)
        return container.name

    def health(self) -> dict[str, object]:
        info: dict[str, object] = {
            "backend": self.backend,
            "image": self.cfg.sandbox_image,
            "network": self.cfg.sandbox_network,
        }
        try:
            if self._client is not None:
                info["ok"] = bool(self._client.ping())
        except Exception:  # noqa: BLE001
            info["ok"] = False
        return info


async def create_sandbox(cfg: Settings, logger: logging.Logger | None = None) -> BaseSandbox:
    logger = logger or log
    if not cfg.sandbox_enabled:
        logger.warning("sandbox disabled — falling back to LocalSandbox (not a security boundary)")
        return LocalSandbox(cfg)
    sandbox = DockerSandbox(cfg)
    try:
        await sandbox.start()
        logger.info("docker sandbox ready (image=%s)", cfg.sandbox_image)
        return sandbox
    except Exception as exc:  # noqa: BLE001
        logger.error("docker sandbox unavailable (%s) — using LocalSandbox", exc)
        return LocalSandbox(cfg)
