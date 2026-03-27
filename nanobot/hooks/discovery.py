from __future__ import annotations

import importlib.util
import logging
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .registry import HookRegistry


logger = logging.getLogger(__name__)


def _iter_hook_entry_points() -> Iterable[Any]:
    """Yield entry points registered under the ``nanobot.hooks`` group.

    This helper wraps the ``importlib.metadata.entry_points`` API to support
    both the modern ``select`` interface and the older mapping-style
    interface that may exist on Python 3.10.
    """

    eps = entry_points()
    if hasattr(eps, "select"):
        # Python 3.11+ style
        return eps.select(group="nanobot.hooks")

    # Older style (<=3.10) where entry_points() returns a mapping
    return eps.get("nanobot.hooks", [])


def discover_entry_points(registry: HookRegistry) -> None:
    """Discover external hook plugins via ``importlib.metadata.entry_points``.

    Each entry point in the ``nanobot.hooks`` group is expected to resolve to
    a callable ``register(registry)`` function. Errors are logged and the
    offending plugin is skipped.
    """

    for ep in _iter_hook_entry_points():
        try:
            register_func = ep.load()
            register_func(registry)
        except Exception as exc:  # pragma: no cover - defensive logging
            name = getattr(ep, "name", "<unknown>")
            logger.warning("Failed to load hook plugin '%s': %s", name, exc)


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """Parse YAML frontmatter from HOOK.md content.

    The frontmatter is expected to be between the first two ``---`` lines.
    We first try to use :mod:`yaml` if available; if not, we fall back to a
    minimal key-value parser that understands the simple cases used by
    nanobot hooks (scalar values and comma-separated lists).
    """

    start = text.find("---")
    if start == -1:
        return {}
    start = text.find("\n", start)
    if start == -1:
        return {}
    end = text.find("\n---", start)
    if end == -1:
        return {}

    frontmatter = text[start + 1 : end]

    # Prefer full YAML parsing when available
    try:  # pragma: no cover - optional dependency
        import yaml  # type: ignore

        data = yaml.safe_load(frontmatter) or {}
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        # Fallback to a very small parser for simple `key: value` pairs.
        data: Dict[str, Any] = {}
        for raw_line in frontmatter.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not value:
                continue
            # Very small subset: comma-separated lists or scalars
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if inner:
                    data[key] = [v.strip() for v in inner.split(",") if v.strip()]
                else:
                    data[key] = []
            else:
                data[key] = value
        return data


def discover_workspace_hooks(registry: HookRegistry, workspace_dir: str) -> None:
    """Discover and register hooks from ``<workspace_dir>/hooks``.

    Each subdirectory under ``hooks`` represents a hook package containing:

    * ``HOOK.md`` - YAML frontmatter describing ``name``, ``events``,
      ``priority`` and ``description``.
    * ``handler.py`` - defines ``async def handler(context)``.

    For every event in ``events``, the function registers the ``handler`` via
    :meth:`HookRegistry.register`. Any error while processing a specific hook
    directory is logged and does not affect others.
    """

    root = Path(workspace_dir) / "hooks"
    if not root.exists() or not root.is_dir():
        return

    for hook_dir in root.iterdir():
        if not hook_dir.is_dir():
            continue

        hook_md = hook_dir / "HOOK.md"
        handler_py = hook_dir / "handler.py"

        if not hook_md.is_file() or not handler_py.is_file():
            logger.warning(
                "Skipping hook directory '%s': missing HOOK.md or handler.py",
                hook_dir,
            )
            continue

        try:
            meta_text = hook_md.read_text(encoding="utf-8")
            meta = _parse_frontmatter(meta_text)

            name = str(meta.get("name") or hook_dir.name)
            events = meta.get("events") or []
            priority = int(meta.get("priority") or 0)

            if isinstance(events, str):
                # Support comma separated string in fallback parser
                events = [e.strip() for e in events.split(",") if e.strip()]

            if not isinstance(events, list) or not events:
                logger.warning(
                    "Skipping hook '%s': invalid or empty 'events' in HOOK.md",
                    hook_dir,
                )
                continue

            # Dynamically import handler.py
            module_name = f"nanobot_workspace_hook_{hook_dir.name}"
            spec = importlib.util.spec_from_file_location(module_name, handler_py)
            if spec is None or spec.loader is None:
                logger.warning(
                    "Skipping hook '%s': unable to create module spec", hook_dir
                )
                continue

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[assignment]

            handler = getattr(module, "handler", None)
            if handler is None:
                logger.warning(
                    "Skipping hook '%s': no 'handler' function found in handler.py",
                    hook_dir,
                )
                continue

            for event in events:
                registry.register(event, handler, priority=priority, name=name)

        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to load workspace hook '%s': %s", hook_dir, exc)
