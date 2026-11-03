"""Exercise real SDK recording and bootstrap without a collector."""
import asyncio

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from miner import telemetry
from miner.config import Settings
from miner.rpc.bitcoin_rpc import BitcoinRpc


@pytest.fixture
def recording(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meters = MeterProvider(metric_readers=[reader])
    meter = meters.get_meter("test")
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("test"))
    monkeypatch.setattr(telemetry, "operations", meter.create_counter("operations"))
    monkeypatch.setattr(telemetry, "duration", meter.create_histogram("duration"))
    yield exporter, reader
    provider.shutdown()
    meters.shutdown()


def test_operation_errors_are_measured_without_payloads(recording):
    exporter, reader = recording
    with pytest.raises(RuntimeError):
        with telemetry.operation("bitcoin.rpc"):
            raise RuntimeError("secret RPC payload")
    span, = exporter.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["error.type"] == "RuntimeError"
    assert not span.events
    assert span.status.description is None
    metrics = reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics
    assert {m.name for m in metrics} == {"operations", "duration"}
    assert all(m.data.data_points[0].attributes["outcome"] == "error" for m in metrics)


def test_rpc_trace_is_nested_and_does_not_export_parameters(recording):
    class Response:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def json(self, **kwargs):
            return {"result": None, "error": None}

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    async def scenario():
        rpc = BitcoinRpc(Settings())
        rpc._session = Session()
        with telemetry.operation("stratum.submit"):
            assert await rpc.call("submitblock", ["private-block-payload"]) is None

    asyncio.run(scenario())
    child, parent = recording[0].get_finished_spans()
    assert child.parent.span_id == parent.context.span_id
    assert child.attributes["rpc.method"] == "submitblock"
    assert "private-block-payload" not in str(child.attributes)


def test_disabled_and_unconfigured_do_not_create_exporters(monkeypatch):
    for key in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    def unexpected(*args, **kwargs):
        pytest.fail("exporter created when disabled")
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", unexpected)
    monkeypatch.setattr(telemetry, "OTLPMetricExporter", unexpected)
    telemetry.configure_telemetry()()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    telemetry.configure_telemetry()()


def test_otlp_http_export_and_shutdown():
    # Separate process: SDK global providers can only be installed once.
    import os
    import subprocess
    import sys
    code = '''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
import os
from miner import telemetry
received = {}
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        received[self.path] = self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass
server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
os.environ['OTEL_EXPORTER_OTLP_ENDPOINT'] = f'http://127.0.0.1:{server.server_port}'
os.environ['OTEL_SERVICE_NAME'] = 'miner-test'
try:
    shutdown = telemetry.configure_telemetry()
    with telemetry.operation('stratum.submit'):
        telemetry.shares.add(1, {'outcome': 'accepted'})
    shutdown()
    traces = ExportTraceServiceRequest.FromString(received['/v1/traces'])
    metrics = ExportMetricsServiceRequest.FromString(received['/v1/metrics'])
    resource = traces.resource_spans[0]
    assert resource.scope_spans[0].spans[0].name == 'stratum.submit'
    assert any(a.key == 'service.name' and a.value.string_value == 'miner-test' for a in resource.resource.attributes)
    names = {m.name for r in metrics.resource_metrics for s in r.scope_metrics for m in s.metrics}
    assert {'miner.shares', 'miner.operations', 'miner.operation.duration'} <= names
finally:
    server.shutdown()
    server.server_close()
    thread.join()
'''
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env["NO_PROXY"] = "127.0.0.1,localhost"
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
