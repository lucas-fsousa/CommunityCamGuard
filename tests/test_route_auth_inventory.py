"""Keep public/custom-auth HTTP routes explicit; inspect metadata, never run cameras."""

import ast
import inspect

from fastapi import APIRouter
from fastapi.routing import APIRoute, APIWebSocketRoute

from backend.app import auth, main
from backend.app.api import native_media
from backend.app.diagnostics.local_operator import require_local_operator

PUBLIC_HTTP = {("/api/login", "POST"), ("/api/logout", "POST"), ("/api/me", "GET"),
               ("/health", "GET"), ("/api/build", "GET")}
SOCKETS = {"/api/go2rtc/ws", "/api/cameras/{camera_id}/intercom/stream",
           "/api/media/native/{camera_id}/ws"}


def routes():
    # FastAPI versions either flatten include_router or retain lazy include objects.
    # Inspect the explicitly wired router objects plus direct app routes on both.
    result = {}
    for value in [main.app, *(item for item in vars(main).values() if isinstance(item, APIRouter))]:
        for route in value.routes:
            if isinstance(value, APIRouter):
                assert isinstance(route, (APIRoute, APIWebSocketRoute)), "review nested router authority"
            if isinstance(route, (APIRoute, APIWebSocketRoute)):
                result[(route.path, route.endpoint)] = route
    return list(result.values())


def dependencies(dependant):
    yield dependant.call
    for child in dependant.dependencies:
        yield from dependencies(child)


def test_every_http_endpoint_has_reviewed_authority():
    tree = ast.parse(inspect.getsource(main))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "include_router":
            assert len(node.args) == 1 and isinstance(node.args[0], ast.Name), "review router wiring"
            assert isinstance(getattr(main, node.args[0].id), APIRouter)
    observed_public = set()
    for route in routes():
        if not isinstance(route, APIRoute):
            continue
        authority = set(dependencies(route.dependant))
        if authority & {auth.require_auth, auth.require_primary_session, require_local_operator}:
            continue
        for method in route.methods:
            assert (route.path, method) in PUBLIC_HTTP, f"unreviewed public route: {method} {route.path}"
            observed_public.add((route.path, method))
    assert observed_public == PUBLIC_HTTP


def test_websocket_exceptions_remain_explicit():
    assert {route.path for route in routes() if isinstance(route, APIWebSocketRoute)} == SOCKETS
    for route in routes():
        if isinstance(route, APIWebSocketRoute):
            source = inspect.getsource(route.endpoint)
            if route.endpoint is native_media.native_video:
                # Explicit reviewed delegation; do not exempt arbitrary wrappers.
                assert "await serve_native_video(socket, camera_id)" in source
                gate = inspect.getsource(native_media.serve_native_video)
                assert 'auth.verify_permission_token(token, "live")' in gate
                assert "browser_origin_allowed(socket)" in gate
                assert "run_guarded(" in gate
            else:
                assert "verify_token(" in source and "browser_origin_allowed(" in source


def test_diagnostic_wrapper_retains_authentication_before_local_checks():
    tree = ast.parse(inspect.getsource(require_local_operator))
    calls = [node.value.func.id for node in tree.body[0].body
             if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
             and isinstance(node.value.func, ast.Name)]
    assert calls[:2] == ["require_auth", "require_local_request"]


def test_broker_read_trigger_retains_both_operator_gates():
    matches = [route for route in routes() if isinstance(route, APIRoute)
               and route.path == "/api/internal/diagnostics/broker-reads"]
    assert len(matches) == 1
    route = matches[0]
    assert route.methods == {"POST"} and not route.include_in_schema
    assert {require_local_operator, auth.require_primary_session} <= set(dependencies(route.dependant))


def test_schema_routes_are_the_only_public_non_api_routes():
    from starlette.routing import Mount, Route
    paths = {route.path for route in main.app.routes if isinstance(route, Route) and not isinstance(route, APIRoute)}
    assert paths == {"/api/openapi.json", "/api/docs", "/docs/oauth2-redirect", "/api/redoc"}
    mounts = [route for route in main.app.routes if isinstance(route, Mount)]
    assert len(mounts) == 1 and mounts[0].name == "frontend"
