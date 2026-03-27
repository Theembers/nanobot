from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


class HookEvent:
    """Hook 事件名称常量，分三大类"""

    # -- Agent 生命周期 hooks --
    AGENT_BOOTSTRAP = "agent_bootstrap"
    SESSION_CREATED = "session_created"
    COMMAND_EXECUTED = "command_executed"
    BEFORE_CONSOLIDATION = "before_consolidation"
    AFTER_CONSOLIDATION = "after_consolidation"

    # -- 消息处理 hooks --
    BEFORE_MESSAGE_DISPATCH = "before_message_dispatch"
    AFTER_MESSAGE_DISPATCH = "after_message_dispatch"
    BEFORE_OUTBOUND_SEND = "before_outbound_send"
    AFTER_SESSION_SAVE = "after_session_save"

    # -- LLM 调用链 hooks --
    BEFORE_LLM_CALL = "before_llm_call"
    AFTER_LLM_RESPONSE = "after_llm_response"
    BEFORE_TOOL_EXECUTION = "before_tool_execution"
    AFTER_TOOL_EXECUTION = "after_tool_execution"


@dataclass
class HookContext:
    """传递给 hook handler 的上下文"""

    event: str
    session_key: str = ""
    channel: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """hook handler 的返回结果"""

    block: bool = False
    modified_data: Optional[dict[str, Any]] = None


# Handler 类型别名
HookHandler = Callable[[HookContext], Awaitable[Optional[HookResult]]]
