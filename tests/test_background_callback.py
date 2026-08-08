import asyncio
from types import SimpleNamespace

from core.drawing.callback import CallbackDispatcher


def build_dispatcher(callback):
    target = SimpleNamespace(on_media_generation_complete=callback)
    context = SimpleNamespace(
        get_registered_star=lambda plugin_name: SimpleNamespace(star_cls=target)
    )
    plugin = SimpleNamespace(
        context=context,
        llm_tools_config=SimpleNamespace(
            background_callback_plugin="upstream_plugin",
            background_callback_method="on_media_generation_complete",
        ),
    )
    return CallbackDispatcher(plugin)


def test_callback_receives_success_state_and_complete_keyword_contract() -> None:
    received = {}

    def callback(**kwargs):
        received.update(kwargs)
        return True

    event = SimpleNamespace(unified_msg_origin="platform:message:session")
    result = object()
    params = {"prompt": "draw a lighthouse"}

    handled = asyncio.run(
        build_dispatcher(callback).dispatch(
            event=event,
            result=result,
            params=params,
            unified_msg_origin="platform:message:session",
            is_success=True,
        )
    )

    assert handled is True
    assert received == {
        "event": event,
        "result": result,
        "params": params,
        "unified_msg_origin": "platform:message:session",
        "is_success": True,
    }


def test_async_callback_receives_failure_state_and_can_decline_handling() -> None:
    received = {}

    async def callback(**kwargs):
        received.update(kwargs)
        return False

    event = SimpleNamespace(unified_msg_origin="platform:message:session")

    handled = asyncio.run(
        build_dispatcher(callback).dispatch(
            event=event,
            result=object(),
            params={"prompt": "draw a lighthouse"},
            unified_msg_origin="platform:message:session",
            is_success=False,
        )
    )

    assert handled is False
    assert received["is_success"] is False


def test_callback_receives_captured_origin_instead_of_current_event_value() -> None:
    received = {}

    def callback(**kwargs):
        received.update(kwargs)
        return True

    event = SimpleNamespace(unified_msg_origin="changed:session")

    handled = asyncio.run(
        build_dispatcher(callback).dispatch(
            event=event,
            result=object(),
            params={"prompt": "draw a lighthouse"},
            unified_msg_origin="original:session",
            is_success=True,
        )
    )

    assert handled is True
    assert received["event"] is event
    assert received["unified_msg_origin"] == "original:session"


