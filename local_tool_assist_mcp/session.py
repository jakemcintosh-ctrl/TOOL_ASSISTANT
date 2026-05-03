"""Session creation, persistence, and review-state management for Local Tool Assist."""

import datetime
import os
import pathlib
import random
import re
import string

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False

_HERE = pathlib.Path(__file__).parent
_TOOLSET_ROOT = _HERE.parent
DEFAULT_OUTPUT_ROOT: pathlib.Path = _TOOLSET_ROOT / "local_tool_assist_outputs"
TOOLCHAIN_ROOT: pathlib.Path = _TOOLSET_ROOT / "aletheia_toolchain"
_OUTPUT_SUBDIRS = ("sessions", "intermediate", "reports", "archive", "logs")

_SESSION_ID_RE = re.compile(r"^lta_[0-9]{8}T[0-9]{6}Z_[a-z0-9]{6}$")

_REVIEW_STATE_FIELDS = frozenset({
    "scan_reviewed",
    "manifest_reviewed",
    "slice_approved",
    "approved_by",
    "approved_at",
    "approval_notes",
})

_SENSITIVE_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.IGNORECASE)


def _resolve_output_root(output_root=None) -> pathlib.Path:
    if output_root is not None:
        return pathlib.Path(output_root)
    env = os.environ.get("LTA_OUTPUT_ROOT")
    if env:
        return pathlib.Path(env)
    return DEFAULT_OUTPUT_ROOT


def _now_str() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _relative_to_session(path_value: str, session_dir: pathlib.Path) -> str:
    if not path_value:
        return ""
    value = pathlib.Path(path_value)
    if not value.is_absolute():
        return value.as_posix()
    try:
        return value.relative_to(session_dir).as_posix()
    except ValueError:
        return value.as_posix()


def _sanitize_for_persistence(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, nested_value in value.items():
            if _SENSITIVE_KEY_RE.search(str(key)):
                continue
            if str(key).lower() in {"environment", "env", "environment_snapshot", "env_snapshot"}:
                continue
            cleaned[key] = _sanitize_for_persistence(nested_value)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_for_persistence(item) for item in value]
    return value


def _build_session_dict(
    session_id: str,
    objective: str,
    target_repo: str,
    requested_by: str,
    downstream_agent: str,
    session_dir: pathlib.Path,
) -> dict:
    now = _now_str()
    return {
        "schema_version": "LocalToolAssistSession/v1.0",
        "session": {
            "id": session_id,
            "created_at": now,
            "updated_at": now,
            "status": "created",
            "review_state": {
                "scan_reviewed": False,
                "manifest_reviewed": False,
                "slice_approved": False,
                "approved_by": "",
                "approved_at": "",
                "approval_notes": "",
            },
        },
        "request": {
            "objective": objective,
            "target_repo": str(target_repo),
            "requested_by": requested_by,
            "downstream_agent": downstream_agent,
        },
        "execution_mode": {
            "autonomy": "guided",
            "require_user_review_before_refine": True,
            "allow_command_execution": False,
        },
        "paths": {
            "session_dir": str(session_dir),
            "output_root": str(session_dir.parent.parent),
            "toolchain_root": str(TOOLCHAIN_ROOT),
        },
        "tool_plan": {
            "policy": {
                "forbid_shell": True,
                "outputs_must_be_outside_toolchain": True,
                "require_manifest_before_slice": True,
                "require_linter_for_generated_commands": True,
                "require_review_before_slice": True,
                "approved_tools": [
                    "create_file_map_v3",
                    "manifest_doctor",
                    "tool_command_linter",
                    "semantic_slicer_v7.0",
                ],
            },
            "steps": [],
        },
        "artifacts": {
            "manifest_csv": "",
            "manifest_health_json": "",
            "manifest_doctor_json": "",
            "manifest_doctor_md": "",
            "command_lint_json": "",
            "slicer_json": "",
            "slicer_md": "",
            "final_markdown": "",
            "final_python_bundle": "",
            "archive_yaml": "",
        },
        "events": [],
    }


def generate_session_id() -> str:
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    short = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"lta_{ts}_{short}"


def create_session(objective: str, target_repo: str, requested_by: str = "user", downstream_agent: str = "unknown", output_root=None) -> tuple:
    root = _resolve_output_root(output_root)
    for subdir in _OUTPUT_SUBDIRS:
        (root / subdir).mkdir(parents=True, exist_ok=True)
    session_id = generate_session_id()
    session_dir = root / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return _build_session_dict(session_id, objective, target_repo, requested_by, downstream_agent, session_dir), session_dir


def save_session(session_dict: dict, yaml_path) -> None:
    if not _HAS_YAML:
        raise ImportError("PyYAML is required for save_session. Install with: pip install PyYAML")
    path = pathlib.Path(yaml_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    persistable = _sanitize_for_persistence(session_dict)
    session_dir = path.parent
    artifacts = persistable.get("artifacts", {})
    if isinstance(artifacts, dict):
        for key, value in list(artifacts.items()):
            if isinstance(value, str):
                artifacts[key] = _relative_to_session(value, session_dir)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        _yaml.dump(persistable, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    os.replace(tmp_path, path)


def load_session(yaml_path) -> dict:
    if not _HAS_YAML:
        raise ImportError("PyYAML is required for load_session. Install with: pip install PyYAML")
    with open(yaml_path, "r", encoding="utf-8") as fh:
        return _yaml.safe_load(fh)


def update_review_state(session_dict: dict, **kwargs) -> dict:
    unknown = set(kwargs) - _REVIEW_STATE_FIELDS
    if unknown:
        raise ValueError(f"Unknown review_state field(s): {sorted(unknown)}")
    session_dict["session"]["review_state"].update(kwargs)
    session_dict["session"]["updated_at"] = _now_str()
    return session_dict
