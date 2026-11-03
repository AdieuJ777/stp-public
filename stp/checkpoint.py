"""P1 检查点：可序列化的执行快照与 JSON 存储。

设计边界：
- 只保存任务数据（输入、状态、已完成节点、计数器、恢复位置），
  不保存 ExecutionContext / 后端 / 工具 / 协程等运行时对象；
- 写入采用临时文件 + fsync + 原子替换，避免把半截文件当成有效检查点；
- 读取时校验文件格式、checkpoint_version 和关键标识，失败必须显式报错。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import (
    CheckpointIOError,
    CheckpointIncompatibleError,
    CheckpointInvalidError,
    CheckpointNotFoundError,
)
from .models import RunError, TaskSpec, utcnow
from .plan import TaskIR

CHECKPOINT_VERSION = 1

CHECKPOINT_KEY_ENV = "STP_CHECKPOINT_KEY"
_CHECKPOINT_ENCRYPTION_FORMAT = "stp-checkpoint-encrypted-v1"
_CHECKPOINT_KDF_SALT = b"stp-checkpoint-kdf-v1"
_CHECKPOINT_KDF_ITERATIONS = 200_000
_CHECKPOINT_AAD = b"stp-checkpoint-v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _checkpoint_content_hash(payload: dict[str, Any]) -> str:
    clone = dict(payload)
    clone["content_hash"] = ""
    # 兼容升级前写入的检查点：当时没有 lineage_id 字段。
    # 空的 lineage_id 不参与哈希，避免旧文件被误判为篡改。
    if not clone.get("lineage_id"):
        clone.pop("lineage_id", None)
    return hashlib.sha256(_canonical_json(clone).encode("utf-8")).hexdigest()


def _derive_encryption_key(passphrase: str) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        _CHECKPOINT_KDF_SALT,
        _CHECKPOINT_KDF_ITERATIONS,
        dklen=32,
    )


def _restrict_permissions(path: Path) -> None:
    """尽量收紧权限；Windows 上 chmod 能力有限，失败不阻断写入。"""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def plan_hash(plan: TaskIR) -> str:
    """执行计划的结构哈希。

    注意：callable 字段被 ``TaskIR.to_dict()`` 排除，因此这是结构哈希，
    不是函数体级别的代码指纹。恢复前仍应保证代码版本一致。
    """
    return hashlib.sha256(_canonical_json(plan.to_dict()).encode("utf-8")).hexdigest()


def spec_hash(spec: TaskSpec) -> str:
    """TaskSpec 的规范化哈希（验证器 callable 不参与）。"""
    return hashlib.sha256(
        _canonical_json(spec.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


class Checkpoint(BaseModel):
    """一次步骤边界上的完整恢复点。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    checkpoint_version: int = CHECKPOINT_VERSION
    checkpoint_id: str = ""
    sequence: int = 0

    run_id: str
    # 跨恢复链稳定的执行谱系标识；旧检查点省略该字段时为空。
    lineage_id: str = ""
    task_id: str
    task_name: str = ""
    task_version: str = ""
    protocol_version: str = "1.0"
    status: str = "RUNNING"
    plan_hash: str
    spec_hash: str

    input: dict[str, Any]
    state: dict[str, Any] = Field(default_factory=dict)
    # 派生只读视图：state 仍是唯一事实来源；旧检查点可以为 None。
    step_results: dict[str, Any] | None = None
    completed_steps: list[str] = Field(default_factory=list)
    branch_routes: dict[str, str] = Field(default_factory=dict)

    step_count: int = 0
    model_call_count: int = 0
    attempt_count: int = 0
    next_step_id: str | None = None
    elapsed_seconds: float = 0.0

    error: RunError | None = None
    unknown_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    resume_safe: bool = True
    resume_block_reason: str | None = None
    content_hash: str = ""

    created_at: datetime = Field(default_factory=utcnow)

    def to_snapshot(self) -> dict[str, Any]:
        """转换为 ExecutionContext.apply_snapshot() 接受的形状。"""
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": self.status,
            "protocol_version": self.protocol_version,
            "input": self.input,
            "state": self.state,
            "step_results": dict(self.step_results)
            if self.step_results is not None
            else None,
            "completed_steps": list(self.completed_steps),
            "branch_routes": dict(self.branch_routes),
            "step_count": self.step_count,
            "model_call_count": self.model_call_count,
            "attempt_count": self.attempt_count,
            "next_step_id": self.next_step_id,
            "elapsed_seconds": self.elapsed_seconds,
        }


class CheckpointStore(Protocol):
    def save(self, checkpoint: Checkpoint) -> str: ...

    def load(self, checkpoint_id: str) -> Checkpoint: ...

    def load_latest(self, run_id: str, task_id: str) -> Checkpoint | None: ...

    def list(self, run_id: str) -> list[Checkpoint]: ...


class JsonCheckpointStore:
    """按 ``<base_dir>/<safe_run_id>/<seq>.json`` 保存检查点。

    默认对检查点做 AES-GCM 加密：密钥优先取 ``STP_CHECKPOINT_KEY``，
    其次使用构造函数传入的 ``encryption_key``，都没有时在 base_dir 下
    生成权限为 0600 的 ``.checkpoint.key``。``encrypt=False`` 仅用于
    调试或旧数据迁移，会在磁盘上留下明文。
    """

    def __init__(
        self,
        base_dir: str | Path,
        *,
        encrypt: bool = True,
        encryption_key: str | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.encrypt = encrypt
        self._aead: AESGCM | None = None
        if encrypt:
            secret = encryption_key or os.environ.get(CHECKPOINT_KEY_ENV)
            if not secret:
                secret = self._load_or_create_key()
            self._aead = AESGCM(_derive_encryption_key(secret))

    def _load_or_create_key(self) -> str:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        key_path = self.base_dir / ".checkpoint.key"
        try:
            existing = key_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except FileNotFoundError:
            pass
        secret = secrets.token_urlsafe(32)
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return key_path.read_text(encoding="utf-8").strip()
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secret)
            handle.flush()
            os.fsync(handle.fileno())
        _restrict_permissions(key_path)
        return secret

    def _encode_text(self, text: str) -> str:
        if self._aead is None:
            return text
        nonce = os.urandom(12)
        ciphertext = self._aead.encrypt(nonce, text.encode("utf-8"), _CHECKPOINT_AAD)
        return json.dumps(
            {
                "format": _CHECKPOINT_ENCRYPTION_FORMAT,
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _decode_text(self, raw: str, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CheckpointInvalidError(
                f"checkpoint file {path} is not valid JSON: {exc}",
                details={"path": str(path)},
                cause=exc,
            ) from exc

        if not isinstance(payload, dict) or payload.get("format") != _CHECKPOINT_ENCRYPTION_FORMAT:
            return payload

        if self._aead is None:
            raise CheckpointInvalidError(
                f"checkpoint file {path} is encrypted; construct the store "
                "with encryption enabled and the matching key",
                details={"path": str(path)},
            )
        try:
            nonce = base64.b64decode(payload["nonce"])
            ciphertext = base64.b64decode(payload["ciphertext"])
            plaintext = self._aead.decrypt(nonce, ciphertext, _CHECKPOINT_AAD)
        except (InvalidTag, KeyError, ValueError, TypeError) as exc:
            raise CheckpointInvalidError(
                f"failed to decrypt checkpoint {path}; wrong key or corrupted file",
                details={"path": str(path)},
                cause=exc,
            ) from exc
        try:
            decoded = json.loads(plaintext.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CheckpointInvalidError(
                f"decrypted checkpoint {path} is not valid JSON: {exc}",
                details={"path": str(path)},
                cause=exc,
            ) from exc
        return decoded

    @staticmethod
    def _safe_component(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
        return safe or "run"

    def _run_dir(self, run_id: str) -> Path:
        return self.base_dir / self._safe_component(run_id)

    def _path(self, checkpoint_id: str) -> Path:
        run_part, sep, seq_part = checkpoint_id.rpartition("-")
        if not sep or not seq_part.isdigit():
            raise CheckpointInvalidError(
                f"invalid checkpoint_id {checkpoint_id!r}",
                details={"checkpoint_id": checkpoint_id},
            )
        return self.base_dir / self._safe_component(run_part) / f"{int(seq_part):06d}.json"

    @staticmethod
    def _next_sequence(run_dir: Path) -> int:
        max_sequence = 0
        for path in run_dir.glob("*.json"):
            try:
                max_sequence = max(max_sequence, int(path.stem))
            except ValueError:
                continue
        return max_sequence + 1

    def save(self, checkpoint: Checkpoint) -> str:
        run_dir = self._run_dir(checkpoint.run_id)
        try:
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(run_dir, 0o700)
            except OSError:
                pass
            sequence = self._next_sequence(run_dir)
            checkpoint_id = f"{self._safe_component(checkpoint.run_id)}-{sequence:06d}"
            checkpoint.sequence = sequence
            checkpoint.checkpoint_id = checkpoint_id
            checkpoint.content_hash = ""

            payload = checkpoint.model_dump(mode="json")
            checkpoint.content_hash = _checkpoint_content_hash(payload)
            payload = checkpoint.model_dump(mode="json")
            text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            text = self._encode_text(text)
            target = run_dir / f"{sequence:06d}.json"

            fd, temp_name = tempfile.mkstemp(
                prefix=".tmp-", suffix=".json", dir=str(run_dir)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, target)
                _restrict_permissions(target)
            except Exception:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise
        except CheckpointError:
            raise
        except Exception as exc:
            raise CheckpointIOError(
                f"failed to save checkpoint: {exc}",
                details={"run_id": checkpoint.run_id},
                cause=exc,
            ) from exc
        return checkpoint_id

    def _read(self, path: Path) -> Checkpoint:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CheckpointIOError(
                f"failed to read checkpoint file {path}: {exc}",
                details={"path": str(path)},
                cause=exc,
            ) from exc

        payload = self._decode_text(raw, path)

        try:
            checkpoint = Checkpoint.model_validate(payload)
        except ValidationError as exc:
            raise CheckpointInvalidError(
                f"checkpoint file {path} does not match the checkpoint schema",
                details={"path": str(path), "errors": exc.errors()},
                cause=exc,
            ) from exc

        if checkpoint.checkpoint_version != CHECKPOINT_VERSION:
            raise CheckpointIncompatibleError(
                f"unsupported checkpoint_version {checkpoint.checkpoint_version}; "
                f"expected {CHECKPOINT_VERSION}",
                details={
                    "path": str(path),
                    "checkpoint_version": checkpoint.checkpoint_version,
                },
            )
        if checkpoint.content_hash:
            payload = checkpoint.model_dump(mode="json")
            computed = _checkpoint_content_hash(payload)
            if computed != checkpoint.content_hash:
                raise CheckpointInvalidError(
                    f"checkpoint content hash mismatch for {path}",
                    details={"path": str(path)},
                )
        return checkpoint

    def load(self, checkpoint_id: str) -> Checkpoint:
        path = self._path(checkpoint_id)
        if not path.exists():
            raise CheckpointNotFoundError(
                f"checkpoint {checkpoint_id!r} was not found",
                details={"checkpoint_id": checkpoint_id, "path": str(path)},
            )
        checkpoint = self._read(path)
        if checkpoint.checkpoint_id and checkpoint.checkpoint_id != checkpoint_id:
            raise CheckpointInvalidError(
                f"checkpoint file {path} contains checkpoint_id "
                f"{checkpoint.checkpoint_id!r}, expected {checkpoint_id!r}",
                details={"path": str(path), "expected": checkpoint_id},
            )
        return checkpoint

    def load_latest(self, run_id: str, task_id: str) -> Checkpoint | None:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            return None
        files = sorted(
            (path for path in run_dir.glob("*.json") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
            reverse=True,
        )
        for path in files:
            checkpoint = self._read(path)
            if checkpoint.run_id == run_id and checkpoint.task_id == task_id:
                return checkpoint
        return None

    def list(self, run_id: str) -> list[Checkpoint]:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            return []
        files = sorted(
            (path for path in run_dir.glob("*.json") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
        )
        return [self._read(path) for path in files]
