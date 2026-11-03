"""Opt-in OTLP/HTTP telemetry. Never attach miner identities or RPC payloads."""

import os
from contextlib import contextmanager
from time import perf_counter

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

tracer = trace.get_tracer("miner")
meter = metrics.get_meter("miner")
operations = meter.create_counter("miner.operations", unit="{operation}")
duration = meter.create_histogram("miner.operation.duration", unit="s")
connections = meter.create_up_down_counter("miner.connections", unit="{connection}")
shares = meter.create_counter("miner.shares", unit="{share}")
blocks = meter.create_counter("miner.block.submissions", unit="{block}")


@contextmanager
def operation(name: str, attributes: dict | None = None, *, kind=trace.SpanKind.INTERNAL):
    """Measure bounded operation names without exporting exception messages/payloads."""
    labels = {"operation": name, **(attributes or {})}
    started = perf_counter()
    outcome = "success"
    with tracer.start_as_current_span(
        name, attributes=labels, kind=kind,
        record_exception=False, set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except BaseException as exc:
            outcome = "cancelled" if not isinstance(exc, Exception) else "error"
            span.set_status(trace.StatusCode.ERROR)
            span.set_attribute("error.type", type(exc).__name__)
            raise
        finally:
            labels["outcome"] = outcome
            operations.add(1, labels)
            duration.record(perf_counter() - started, labels)


def configure_telemetry():
    """Return a shutdown callback; enable each signal only with an OTLP endpoint.

    Standard exporter environment variables configure HTTP/protobuf endpoints,
    authentication, timeouts and TLS. SDK variables configure sampling/resources.
    """
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return lambda: None
    resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "prometejs-miner")})
    providers = []
    try:
        if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
            provider = TracerProvider(resource=resource)
            providers.append(provider)
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(provider)
        if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"):
            reader = PeriodicExportingMetricReader(OTLPMetricExporter())
            provider = MeterProvider(resource=resource, metric_readers=[reader])
            providers.append(provider)
            metrics.set_meter_provider(provider)
    except Exception:
        for provider in reversed(providers):
            provider.shutdown()
        raise

    def shutdown():
        for provider in reversed(providers):
            provider.shutdown()

    return shutdown
