"""Tests for the nanobot hooks system."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nanobot.hooks import HookContext, HookEvent, HookRegistry, HookResult
from nanobot.hooks.discovery import discover_entry_points, discover_workspace_hooks


class TestHookRegistryBasic:
    """Test basic HookRegistry functionality."""

    @pytest.mark.asyncio
    async def test_register_and_call(self) -> None:
        """Register a handler, call(), verify handler is executed."""
        registry = HookRegistry()
        called: list[str] = []

        async def handler(ctx: HookContext) -> None:
            called.append("executed")

        registry.register("test_event", handler)
        ctx = HookContext(event="test_event")
        result = await registry.call("test_event", ctx)

        assert called == ["executed"]
        assert result is ctx

    @pytest.mark.asyncio
    async def test_call_no_handlers(self) -> None:
        """When no handlers registered, call() returns original context."""
        registry = HookRegistry()
        ctx = HookContext(event="no_handlers", data={"original": "data"})

        result = await registry.call("no_handlers", ctx)

        assert result is ctx
        assert result.data == {"original": "data"}

    @pytest.mark.asyncio
    async def test_unregister(self) -> None:
        """After unregister, handler should not be called."""
        registry = HookRegistry()
        called: list[str] = []

        async def handler(ctx: HookContext) -> None:
            called.append("executed")

        registry.register("test_event", handler, name="my_handler")
        registry.unregister("test_event", "my_handler")

        ctx = HookContext(event="test_event")
        await registry.call("test_event", ctx)

        assert called == []


class TestHookRegistryPriority:
    """Test priority ordering in HookRegistry."""

    @pytest.mark.asyncio
    async def test_priority_ordering(self) -> None:
        """Higher priority handlers execute first."""
        registry = HookRegistry()
        order: list[str] = []

        async def handler_low(ctx: HookContext) -> None:
            order.append("low")

        async def handler_high(ctx: HookContext) -> None:
            order.append("high")

        async def handler_medium(ctx: HookContext) -> None:
            order.append("medium")

        registry.register("test_event", handler_low, priority=1, name="low")
        registry.register("test_event", handler_high, priority=10, name="high")
        registry.register("test_event", handler_medium, priority=5, name="medium")

        ctx = HookContext(event="test_event")
        await registry.call("test_event", ctx)

        assert order == ["high", "medium", "low"]


class TestHookRegistryBlocking:
    """Test blocking mechanism in HookRegistry."""

    @pytest.mark.asyncio
    async def test_block_stops_chain(self) -> None:
        """Handler returning block=True stops subsequent handlers."""
        registry = HookRegistry()
        executed: list[str] = []

        async def handler_a(ctx: HookContext) -> HookResult:
            executed.append("A")
            return HookResult(block=True)

        async def handler_b(ctx: HookContext) -> None:
            executed.append("B")

        registry.register("test_event", handler_a, priority=10, name="A")
        registry.register("test_event", handler_b, priority=1, name="B")

        ctx = HookContext(event="test_event")
        await registry.call("test_event", ctx)

        assert executed == ["A"]
        assert "B" not in executed


class TestHookRegistryErrorIsolation:
    """Test error handling in HookRegistry."""

    @pytest.mark.asyncio
    async def test_error_isolation(self) -> None:
        """Exception in one handler doesn't stop others."""
        registry = HookRegistry()
        executed: list[str] = []

        async def handler_failing(ctx: HookContext) -> None:
            executed.append("A")
            raise RuntimeError("handler A failed")

        async def handler_ok(ctx: HookContext) -> None:
            executed.append("B")

        registry.register("test_event", handler_failing, priority=10, name="A")
        registry.register("test_event", handler_ok, priority=1, name="B")

        ctx = HookContext(event="test_event")
        # Should not raise
        result = await registry.call("test_event", ctx)

        assert "A" in executed
        assert "B" in executed
        assert result is ctx


class TestHookRegistryDataModification:
    """Test modified_data merging in HookRegistry."""

    @pytest.mark.asyncio
    async def test_modified_data_merge(self) -> None:
        """Handler can modify context.data via HookResult."""
        registry = HookRegistry()

        async def handler(ctx: HookContext) -> HookResult:
            return HookResult(modified_data={"new_key": "new_value", "count": 42})

        registry.register("test_event", handler)

        ctx = HookContext(event="test_event", data={"original": "data"})
        result = await registry.call("test_event", ctx)

        assert result.data == {
            "original": "data",
            "new_key": "new_value",
            "count": 42,
        }


class TestDiscoverWorkspaceHooks:
    """Test workspace hooks discovery."""

    def test_discover_workspace_hooks(self, tmp_path: Path) -> None:
        """Discover hooks from workspace/hooks/ directory structure."""
        # Create hook directory structure
        hooks_dir = tmp_path / "hooks"
        my_hook_dir = hooks_dir / "my_hook"
        my_hook_dir.mkdir(parents=True)

        # Create HOOK.md with frontmatter
        hook_md = my_hook_dir / "HOOK.md"
        hook_md.write_text(
            """---
name: my_test_hook
events:
  - test_event
  - another_event
priority: 5
description: A test hook
---
# My Test Hook
""",
            encoding="utf-8",
        )

        # Create handler.py
        handler_py = my_hook_dir / "handler.py"
        handler_py.write_text(
            """
from nanobot.hooks import HookContext, HookResult

async def handler(ctx: HookContext) -> HookResult:
    ctx.data["hook_ran"] = True
    return None
""",
            encoding="utf-8",
        )

        registry = HookRegistry()
        discover_workspace_hooks(registry, str(tmp_path))

        # Verify handler was registered for both events
        assert "test_event" in registry._hooks
        assert "another_event" in registry._hooks

        # Verify handler name
        entries = registry._hooks.get("test_event", [])
        assert len(entries) == 1
        assert entries[0].name == "my_test_hook"
        assert entries[0].priority == 5

    def test_discover_workspace_hooks_with_yaml_list_string(
        self, tmp_path: Path
    ) -> None:
        """Test that comma-separated events string is handled."""
        hooks_dir = tmp_path / "hooks"
        my_hook_dir = hooks_dir / "string_events_hook"
        my_hook_dir.mkdir(parents=True)

        hook_md = my_hook_dir / "HOOK.md"
        # Use fallback parser format (comma-separated string without YAML list)
        hook_md.write_text(
            """---
name: string_hook
events: event_one, event_two, event_three
priority: 3
---
""",
            encoding="utf-8",
        )

        handler_py = my_hook_dir / "handler.py"
        handler_py.write_text(
            """
from nanobot.hooks import HookContext

async def handler(ctx: HookContext):
    pass
""",
            encoding="utf-8",
        )

        registry = HookRegistry()
        discover_workspace_hooks(registry, str(tmp_path))

        assert "event_one" in registry._hooks
        assert "event_two" in registry._hooks
        assert "event_three" in registry._hooks

    def test_discover_workspace_missing_hook_md(self, tmp_path: Path) -> None:
        """Missing HOOK.md should skip the directory without error."""
        hooks_dir = tmp_path / "hooks"
        my_hook_dir = hooks_dir / "incomplete_hook"
        my_hook_dir.mkdir(parents=True)

        # Only create handler.py, no HOOK.md
        handler_py = my_hook_dir / "handler.py"
        handler_py.write_text("async def handler(ctx): pass", encoding="utf-8")

        registry = HookRegistry()
        # Should not raise
        discover_workspace_hooks(registry, str(tmp_path))

        # Nothing should be registered
        assert not registry._hooks

    def test_discover_workspace_missing_handler_py(self, tmp_path: Path) -> None:
        """Missing handler.py should skip the directory without error."""
        hooks_dir = tmp_path / "hooks"
        my_hook_dir = hooks_dir / "incomplete_hook"
        my_hook_dir.mkdir(parents=True)

        # Only create HOOK.md, no handler.py
        hook_md = my_hook_dir / "HOOK.md"
        hook_md.write_text(
            """---
name: incomplete
events: [test_event]
---
""",
            encoding="utf-8",
        )

        registry = HookRegistry()
        # Should not raise
        discover_workspace_hooks(registry, str(tmp_path))

        assert not registry._hooks

    def test_discover_workspace_no_hooks_dir(self, tmp_path: Path) -> None:
        """No hooks directory should not error."""
        registry = HookRegistry()
        # Should not raise
        discover_workspace_hooks(registry, str(tmp_path))

        assert not registry._hooks

    def test_discover_workspace_empty_events(self, tmp_path: Path) -> None:
        """Empty events list should skip the hook."""
        hooks_dir = tmp_path / "hooks"
        my_hook_dir = hooks_dir / "empty_events_hook"
        my_hook_dir.mkdir(parents=True)

        hook_md = my_hook_dir / "HOOK.md"
        hook_md.write_text(
            """---
name: empty_events
events: []
---
""",
            encoding="utf-8",
        )

        handler_py = my_hook_dir / "handler.py"
        handler_py.write_text("async def handler(ctx): pass", encoding="utf-8")

        registry = HookRegistry()
        discover_workspace_hooks(registry, str(tmp_path))

        assert not registry._hooks


class TestDiscoverEntryPoints:
    """Test entry points discovery."""

    def test_discover_entry_points(self) -> None:
        """Entry points are discovered and register function is called."""
        registry = HookRegistry()
        register_calls: list[HookRegistry] = []

        def mock_register(reg: HookRegistry) -> None:
            register_calls.append(reg)

        # Create mock entry point
        mock_ep = MagicMock()
        mock_ep.load.return_value = mock_register
        mock_ep.name = "test_plugin"

        with patch(
            "nanobot.hooks.discovery._iter_hook_entry_points",
            return_value=[mock_ep],
        ):
            discover_entry_points(registry)

        assert len(register_calls) == 1
        assert register_calls[0] is registry

    def test_discover_entry_points_error(self) -> None:
        """Failing entry point doesn't affect others."""
        registry = HookRegistry()
        register_calls: list[HookRegistry] = []

        def good_register(reg: HookRegistry) -> None:
            register_calls.append(reg)

        # Create one failing and one working entry point
        failing_ep = MagicMock()
        failing_ep.load.side_effect = ImportError("missing dependency")
        failing_ep.name = "failing_plugin"

        good_ep = MagicMock()
        good_ep.load.return_value = good_register
        good_ep.name = "good_plugin"

        with patch(
            "nanobot.hooks.discovery._iter_hook_entry_points",
            return_value=[failing_ep, good_ep],
        ):
            # Should not raise
            discover_entry_points(registry)

        # Good entry point should still be processed
        assert len(register_calls) == 1
        assert register_calls[0] is registry

    def test_discover_entry_points_empty(self) -> None:
        """No entry points should not error."""
        registry = HookRegistry()

        with patch(
            "nanobot.hooks.discovery._iter_hook_entry_points",
            return_value=[],
        ):
            discover_entry_points(registry)

        assert not registry._hooks


class TestHookContext:
    """Test HookContext dataclass."""

    def test_hook_context_defaults(self) -> None:
        """Test default values for HookContext."""
        ctx = HookContext(event="test")
        assert ctx.event == "test"
        assert ctx.session_key == ""
        assert ctx.channel == ""
        assert ctx.data == {}
        assert ctx.metadata == {}

    def test_hook_context_with_values(self) -> None:
        """Test HookContext with custom values."""
        ctx = HookContext(
            event="test_event",
            session_key="session-123",
            channel="telegram",
            data={"key": "value"},
            metadata={"meta": "data"},
        )
        assert ctx.event == "test_event"
        assert ctx.session_key == "session-123"
        assert ctx.channel == "telegram"
        assert ctx.data == {"key": "value"}
        assert ctx.metadata == {"meta": "data"}


class TestHookResult:
    """Test HookResult dataclass."""

    def test_hook_result_defaults(self) -> None:
        """Test default values for HookResult."""
        result = HookResult()
        assert result.block is False
        assert result.modified_data is None

    def test_hook_result_with_values(self) -> None:
        """Test HookResult with custom values."""
        result = HookResult(block=True, modified_data={"key": "value"})
        assert result.block is True
        assert result.modified_data == {"key": "value"}


class TestHookEvent:
    """Test HookEvent constants."""

    def test_hook_event_constants(self) -> None:
        """Test that HookEvent has expected constants."""
        assert HookEvent.AGENT_BOOTSTRAP == "agent_bootstrap"
        assert HookEvent.SESSION_CREATED == "session_created"
        assert HookEvent.BEFORE_MESSAGE_DISPATCH == "before_message_dispatch"
        assert HookEvent.AFTER_MESSAGE_DISPATCH == "after_message_dispatch"
        assert HookEvent.BEFORE_LLM_CALL == "before_llm_call"
        assert HookEvent.AFTER_LLM_RESPONSE == "after_llm_response"
        assert HookEvent.BEFORE_TOOL_EXECUTION == "before_tool_execution"
        assert HookEvent.AFTER_TOOL_EXECUTION == "after_tool_execution"
