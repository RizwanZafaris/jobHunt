"""
api/observability.py — opt-in backend observability (P3-2).

Three independent initializers, each a hard NO-OP unless its env var is set:

    init_logging()    JSON logs only when LOG_JSON is truthy.
    init_sentry()     Error tracking only when SENTRY_DSN is set.
    init_otel(app)    OTel traces/metrics only when OTEL_EXPORTER_OTLP_ENDPOINT is set.

Design rules (so dev + single-user prod stay byte-for-byte unchanged):

  * Every third-party import (sentry_sdk, opentelemetry, pythonjsonlogger)
    lives INSIDE the init function — never at module top. This module must
    import cleanly even when the observability wheels aren't installed yet.
  * Each init early-returns when its env var is unset, so calling all three
    on every boot is free in the default configuration.
  * Each init is best-effort and idempotent: a failure to wire telemetry must
    never crash the API. We log a warning and continue.
  * PII safety: Sentry runs with send_default_pii=False so request bodies
    (which carry resume / job-description text) are never shipped off-box.

Wired at the top of api/server.py, right after the FastAPI app is created and
BEFORE any add_middleware() call — OTel's FastAPI instrumentation installs its
own ASGI middleware and must sit at the bottom of the stack.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Paths excluded from request tracing: high-frequency liveness/readiness probes
# (Railway hits /health every few seconds) would otherwise drown the trace
# stream in uninteresting spans and inflate metrics cardinality.
_OTEL_EXCLUDED_URLS = "health,healthz,ready,readyz"

# Module-level guards so repeated calls (e.g. test re-imports, double startup)
# don't double-install handlers / instrumentors.
_logging_initialized = False
_sentry_initialized = False
_otel_initialized = False


def _is_truthy(val: str | None) -> bool:
    """Treat the usual on/off spellings consistently with the rest of the app."""
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def init_logging() -> None:
    """Switch the root logger to structured JSON output.

    NO-OP unless LOG_JSON is truthy — the default (plain text) console logging
    that dev + single-user prod rely on is left completely untouched.

    Idempotent: a second call does nothing once JSON logging is installed.
    """
    global _logging_initialized
    if _logging_initialized:
        return
    if not _is_truthy(os.environ.get("LOG_JSON")):
        return

    try:
        # python-json-logger moved the formatter module in 3.1.0
        # (pythonjsonlogger.jsonlogger -> pythonjsonlogger.json). Import
        # defensively so either pinned version works.
        try:
            from pythonjsonlogger.json import JsonFormatter  # >=3.1.0
        except ImportError:  # pragma: no cover - depends on installed version
            from pythonjsonlogger.jsonlogger import JsonFormatter  # 2.x

        handler = logging.StreamHandler()
        handler.setFormatter(
            JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
            )
        )
        root = logging.getLogger()
        # Replace existing handlers so we don't emit each line twice (once
        # plain, once JSON). LOG_LEVEL keeps parity with a typical uvicorn setup.
        root.handlers = [handler]
        level = os.environ.get("LOG_LEVEL", "INFO").upper()
        root.setLevel(getattr(logging, level, logging.INFO))
        _logging_initialized = True
        logger.info("observability: JSON logging enabled")
    except Exception as e:  # pragma: no cover - defensive, never crash boot
        logger.warning("observability: init_logging failed (non-fatal): %s", e)


def init_sentry() -> None:
    """Initialise Sentry error tracking.

    NO-OP unless SENTRY_DSN is set. PII is never sent (send_default_pii=False)
    so request bodies carrying resume / JD text stay on-box. Traces are sampled
    via SENTRY_TRACES_SAMPLE_RATE (default 0.0 — errors only).

    Idempotent and best-effort: failures are logged, never raised.
    """
    global _sentry_initialized
    if _sentry_initialized:
        return
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return

    try:
        import sentry_sdk

        try:
            traces_sample_rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0"))
        except ValueError:
            traces_sample_rate = 0.0

        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
            release=os.environ.get("SENTRY_RELEASE") or os.environ.get("RAILWAY_GIT_COMMIT_SHA"),
            # Never ship resume / job-description text or auth headers.
            send_default_pii=False,
            traces_sample_rate=traces_sample_rate,
        )
        _sentry_initialized = True
        logger.info("observability: Sentry initialised (pii=off, traces=%.2f)", traces_sample_rate)
    except Exception as e:  # pragma: no cover - defensive, never crash boot
        logger.warning("observability: init_sentry failed (non-fatal): %s", e)


def init_otel(app) -> None:
    """Instrument the FastAPI app + outbound httpx with OpenTelemetry.

    NO-OP unless OTEL_EXPORTER_OTLP_ENDPOINT is set. When enabled it:
      * installs an OTLP/HTTP span exporter (BatchSpanProcessor),
      * auto-instruments FastAPI (excluding /health + /ready probes),
      * auto-instruments httpx so every provider/Supabase call is a child span.

    MUST be called BEFORE any app.add_middleware() in server.py: FastAPI
    instrumentation adds its own ASGI middleware and needs to be outermost.

    Idempotent and best-effort: failures are logged, never raised.
    """
    global _otel_initialized
    if _otel_initialized:
        return
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        service_name = os.environ.get("OTEL_SERVICE_NAME", "jobhunt-api")
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        # The exporter reads OTEL_EXPORTER_OTLP_ENDPOINT (+ *_HEADERS) from the
        # environment per the OTel spec, so no endpoint is hard-coded here.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)

        # excluded_urls keeps liveness/readiness probes out of the trace stream.
        FastAPIInstrumentor.instrument_app(app, excluded_urls=_OTEL_EXCLUDED_URLS)
        HTTPXClientInstrumentor().instrument()

        _otel_initialized = True
        logger.info("observability: OpenTelemetry enabled (service=%s)", service_name)
    except Exception as e:  # pragma: no cover - defensive, never crash boot
        logger.warning("observability: init_otel failed (non-fatal): %s", e)
