from types import SimpleNamespace

from web.web_api import BigBananaWebApi


class FakeContext:
    def __init__(self) -> None:
        self.registered_web_apis = []

    def register_web_api(self, route, handler, methods, description) -> None:
        self.registered_web_apis.append((route, handler, methods, description))


def build_web_api(context: FakeContext) -> BigBananaWebApi:
    plugin = SimpleNamespace(context=context)
    return BigBananaWebApi(plugin)


def test_unregister_routes_only_removes_current_instance() -> None:
    context = FakeContext()
    old_web_api = build_web_api(context)
    current_web_api = build_web_api(context)

    old_web_api.register_routes()
    current_web_api.register_routes()
    unrelated_route = ("/another-plugin/config", object(), ["GET"], "unrelated")
    context.registered_web_apis.append(unrelated_route)

    old_web_api.unregister_routes()

    assert len(context.registered_web_apis) == 8
    assert unrelated_route in context.registered_web_apis
    assert all(
        getattr(handler, "__self__", None) is not old_web_api
        for _route, handler, _methods, _description in context.registered_web_apis
    )
    assert sum(
        getattr(handler, "__self__", None) is current_web_api
        for _route, handler, _methods, _description in context.registered_web_apis
    ) == 7
