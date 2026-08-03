from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


APP_NAME = "quota-ring"
LEGACY_APP_NAME = "codex-usage-indicator"
DEFAULT_POLL_SECONDS = 300
DEFAULT_LOW_POLL_SECONDS = 90


@dataclass(frozen=True)
class Config:
    codex_command: str = "codex"
    kimi_command: str = "kimi"
    claude_command: str = "claude"
    codex_enabled: bool = True
    kimi_enabled: bool = True
    claude_enabled: bool = True
    poll_seconds: int = DEFAULT_POLL_SECONDS
    low_poll_seconds: int = DEFAULT_LOW_POLL_SECONDS
    request_timeout_seconds: int = 15
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        override = os.environ.get("QUOTA_RING_CONFIG") or os.environ.get(
            "CODEX_USAGE_INDICATOR_CONFIG"
        )
        config_path = path or Path(
            override or Path.home() / ".config" / APP_NAME / "config.json"
        )
        source_path = config_path
        if path is None and override is None and not config_path.exists():
            legacy_path = (
                Path.home() / ".config" / LEGACY_APP_NAME / "config.json"
            )
            if legacy_path.exists():
                source_path = legacy_path
        values: dict[str, object] = {}
        try:
            values = json.loads(source_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read {source_path}: {exc}") from exc
        return cls(
            codex_command=str(values.get("codex_command") or "codex"),
            kimi_command=str(values.get("kimi_command") or "kimi"),
            claude_command=str(values.get("claude_command") or "claude"),
            codex_enabled=_boolean(values.get("codex_enabled"), True),
            kimi_enabled=_boolean(values.get("kimi_enabled"), True),
            claude_enabled=_boolean(values.get("claude_enabled"), True),
            poll_seconds=_positive_int(values.get("poll_seconds"), DEFAULT_POLL_SECONDS),
            low_poll_seconds=_positive_int(
                values.get("low_poll_seconds"), DEFAULT_LOW_POLL_SECONDS
            ),
            request_timeout_seconds=_positive_int(
                values.get("request_timeout_seconds"), 15
            ),
            path=config_path,
        )

    def save(self) -> None:
        if self.path is None:
            raise ValueError("No configuration path is set")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload.pop("path")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".config-", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
                json.dump(payload, config_file, indent=2)
                config_file.write("\n")
                config_file.flush()
                os.fsync(config_file.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary_path.unlink(missing_ok=True)
            raise


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _boolean(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default
