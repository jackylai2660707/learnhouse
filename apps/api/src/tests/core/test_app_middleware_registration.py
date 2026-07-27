from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

import app as app_module
from src.core.middleware.request_logging import RequestLoggingMiddleware
from src.security.csrf import CSRFProtectionMiddleware


def test_core_app_registers_request_logging_cors_and_csrf_once():
    middleware_classes = [middleware.cls for middleware in app_module.app.user_middleware]

    assert middleware_classes.count(CSRFProtectionMiddleware) == 1
    assert middleware_classes.count(CORSMiddleware) == 1
    assert middleware_classes.count(GZipMiddleware) == 1
    assert middleware_classes.count(RequestLoggingMiddleware) == 1
    assert middleware_classes.index(RequestLoggingMiddleware) < middleware_classes.index(
        CORSMiddleware
    )
    assert middleware_classes.index(CORSMiddleware) < middleware_classes.index(
        CSRFProtectionMiddleware
    )
