from types import SimpleNamespace
from unittest.mock import Mock

import astrbot.api.message_components as Comp

from core.commands.drawing.handler import DrawingCommandHandler
from core.schemas import GenerationResult, ImageResource
from core.utils import (
    build_message_chain,
    build_reply_component,
    build_result_message_chain,
    get_message_id,
)


def test_get_message_id_and_build_reply_with_valid_event() -> None:
    event = SimpleNamespace(message_obj=SimpleNamespace(message_id="msg_123"))
    assert get_message_id(event) == "msg_123"
    reply = build_reply_component(event)
    assert reply is not None
    assert reply.id == "msg_123"


def test_get_message_id_and_build_reply_without_message_obj() -> None:
    event = SimpleNamespace()
    assert get_message_id(event) is None
    assert build_reply_component(event) is None


def test_get_message_id_with_non_string_message_id() -> None:
    event = SimpleNamespace(message_obj=SimpleNamespace(message_id=12345))
    assert get_message_id(event) == "12345"


def test_get_message_id_with_zero_id() -> None:
    event = SimpleNamespace(message_obj=SimpleNamespace(message_id=0))
    assert get_message_id(event) == "0"
    reply = build_reply_component(event)
    assert reply is not None
    assert reply.id == "0"


def test_get_message_id_and_build_reply_with_mock_raising_attribute_error() -> None:
    # 模拟 Mock 对象，访问 message_obj 显式抛出 AttributeError
    mock_event = Mock(spec=["unified_msg_origin", "platform_meta"])
    assert get_message_id(mock_event) is None
    assert build_reply_component(mock_event) is None


def test_build_result_message_chain_without_message_obj() -> None:
    mock_event = Mock(spec=["unified_msg_origin", "platform_meta"])
    mock_event.platform_meta = SimpleNamespace(name="qq")

    result = GenerationResult(images=[SimpleNamespace(bytes=b"dummy", base64=None)])
    chain = build_result_message_chain(mock_event, result, quote_reply_mode="both")

    # 校验结果消息链中不包含 Comp.Reply
    assert not any(isinstance(comp, Comp.Reply) for comp in chain)


def test_quote_reply_mode_options() -> None:
    event = SimpleNamespace(message_obj=SimpleNamespace(message_id="msg_123"))

    # both
    assert build_reply_component(event, quote_reply_mode="both", is_command=True) is not None
    assert build_reply_component(event, quote_reply_mode="both", is_command=False) is not None

    # command_only
    assert build_reply_component(event, quote_reply_mode="command_only", is_command=True) is not None
    assert build_reply_component(event, quote_reply_mode="command_only", is_command=False) is None

    # tool_only
    assert build_reply_component(event, quote_reply_mode="tool_only", is_command=True) is None
    assert build_reply_component(event, quote_reply_mode="tool_only", is_command=False) is not None

    # none
    assert build_reply_component(event, quote_reply_mode="none", is_command=True) is None
    assert build_reply_component(event, quote_reply_mode="none", is_command=False) is None


def test_build_message_chain_helper() -> None:
    event = SimpleNamespace(message_obj=SimpleNamespace(message_id="msg_123"))

    chain = build_message_chain(event, Comp.Plain("hello"), quote_reply_mode="both", is_command=True)
    assert len(chain) == 2
    assert isinstance(chain[0], Comp.Reply)
    assert chain[0].id == "msg_123"
    assert isinstance(chain[1], Comp.Plain)

    chain_none = build_message_chain(event, Comp.Plain("hello"), quote_reply_mode="none", is_command=True)
    assert len(chain_none) == 1
    assert isinstance(chain_none[0], Comp.Plain)




