from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional
import logging

from .types import HookContext, HookHandler, HookResult


logger = logging.getLogger(__name__)


@dataclass
class _HookEntry:
    name: str
    handler: HookHandler
    priority: int = 0


class HookRegistry:
    """Registry for hook handlers keyed by event name."""

    def __init__(self) -> None:
        self._hooks: Dict[str, List[_HookEntry]] = {}

    def register(
        self,
        event: str,
        handler: HookHandler,
        *,
        priority: int = 0,
        name: str | None = None,
    ) -> None:
        """Register a *handler* for *event* with optional *priority* and *name*.

        Handlers with higher priority are executed earlier.
        """

        if name is None or not name:
            name = getattr(handler, "__name__", "<anonymous>")

        entry = _HookEntry(name=name, handler=handler, priority=priority)
        self._hooks.setdefault(event, []).append(entry)

    def unregister(self, event: str, name: str) -> None:
        """Unregister handler(s) for *event* by *name*.

        If the event or handler name does not exist, this is a no-op.
        """

        entries = self._hooks.get(event)
        if not entries:
            return

        self._hooks[event] = [e for e in entries if e.name != name]
        if not self._hooks[event]:
            # Clean up empty list to keep the mapping small
            self._hooks.pop(event, None)

    async def call(self, event: str, context: HookContext) -> HookContext:
        """Execute all handlers registered for *event* with the given *context*.

        Handlers are looked up lazily from the current registry state and
        executed in order of descending priority. Exceptions in individual
        handlers are logged and do not stop execution of remaining handlers.

        A handler may return :class:`HookResult` to influence control flow:

        * ``block=True`` stops execution of subsequent handlers.
        * ``modified_data`` is merged into ``context.data``.
        """

        entries = self._hooks.get(event) or []
        # Sort by priority on every invocation so that dynamic changes are respected.
        for entry in sorted(entries, key=lambda e: e.priority, reverse=True):
            try:
                result: Optional[HookResult] | None = await entry.handler(context)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning(
                    "Hook handler '%s' for event '%s' raised an exception: %s",
                    entry.name,
                    event,
                    exc,
                )
                continue

            if not result:
                continue

            if result.modified_data:
                context.data.update(result.modified_data)

            if result.block:
                break

        return context
