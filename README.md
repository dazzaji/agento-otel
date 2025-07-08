# Memo to Add OpenTelemetry to Agento Modules

> **From:** *Helper 1*
> **To:** *Helper 2*
> **Date:** 2025-07-08

Hello **Helper 2**,

We’re collaborating on **Agento**, a multi‑vendor LLM pipeline that produces project plans. My role (Helper 1) is to design observability; your role is to implement it.

### Mission, in one sentence

Emit **standard OTLP traces** that (1) stream live to an OTEL Collector UI and (2) are archived as OTLP‑JSON files under `/data`, one per run, timestamped. Lake Merritt ingests plain OTLP, so if we stay within spec it will work automatically.

---

### 1. Copy‑paste setup block — start every module with this

```python
# === OTEL SETUP | COPY AS‑IS ===
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode

resource = Resource.create({
    "service.name": "agento",
    "service.version": "1.0.0",
    "agento.module": "1_01_plan_structure",
    "deployment.environment": os.getenv("ENVIRONMENT", "development")
})

provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint="http://localhost:4317"),
        max_export_batch_size=512
    )
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)
# === END SETUP BLOCK ===
```

*(Update `agento.module` per file.)*

---

### 2. Why we instrument

The telemetry **is the product**: it must capture every meaningful decision, prompt, response, retry, error and guard‑rail so later analyses can judge goal‑fitness, agent behaviour and performance.

---

### 3. Gen‑AI semantic conventions — quick reference

| Attribute                        | When to set    | Example                  |
| -------------------------------- | -------------- | ------------------------ |
| `gen_ai.system`                  | Every LLM span | `"gemini"`               |
| `gen_ai.request.model`           | Input          | `"gemini-1.5-pro"`       |
| `gen_ai.response.model`          | Output         | `"gemini-1.5-pro"`       |
| `gen_ai.request.temperature`     | Input          | `0.1`                    |
| `gen_ai.usage.prompt_tokens`     | After response | `prompt_token_count`     |
| `gen_ai.usage.completion_tokens` | After response | `completion_token_count` |
| `gen_ai.response.finish_reasons` | After response | `["stop"]`               |

See spec for full list ([opentelemetry.io][2], [opentelemetry.io][8]).

---

### 4. Helpers

```python
# truncate big responses
def set_ai_response_attribute(span, txt:str):
    if len(txt) > 8192:
        span.set_attribute("gen_ai.response.truncated", True)
        span.set_attribute("gen_ai.response.length", len(txt))
        span.set_attribute("gen_ai.response.content", txt[:8000] + "...[truncated]")
    else:
        span.set_attribute("gen_ai.response.content", txt)

# graceful shutdown for multiproc / SIGTERM
def exit_gracefully(signum=None, frame=None):
    span = trace.get_current_span()
    if span:
        span.set_status(Status(StatusCode.OK))
        span.end()
    trace.get_tracer_provider().force_flush()
    sys.exit(0)

signal.signal(signal.SIGTERM, exit_gracefully)
```

---

### 5. Instrumentation patterns

#### 5.1 LLM call with retries

```python
with tracer.start_as_current_span("llm.gemini.generate_with_retry") as retry_span:
    retry_span.set_attribute("retry.max_attempts", max_retries)
    for attempt in range(max_retries):
        with tracer.start_as_current_span(f"attempt_{attempt}") as attempt_span:
            attempt_span.set_attribute("retry.attempt", attempt)
            attempt_span.set_attribute("gen_ai.system", "gemini")
            attempt_span.set_attribute("gen_ai.request.model", "gemini-1.5-pro")
            attempt_span.set_attribute("gen_ai.request.temperature", 0.1)

            try:
                response = await generate_content(prompt)
                set_ai_response_attribute(attempt_span, response.text)
                break
            except Exception as e:
                attempt_span.record_exception(e)
                attempt_span.set_status(Status(StatusCode.ERROR, str(e)))
                attempt_span.set_attribute("error.type", type(e).__name__)
                if attempt + 1 == max_retries:
                    raise
                await asyncio.sleep(backoff(attempt))
```

#### 5.2 Context propagation between modules

```python
# END of module 1
os.environ['OTEL_TRACE_ID'] = f"{span.get_span_context().trace_id:032x}"
os.environ['OTEL_SPAN_ID']  = f"{span.get_span_context().span_id:016x}"

# START of module 2
from opentelemetry.propagate import TraceContextTextMapPropagator
carrier = {"traceparent": f"00-{os.environ['OTEL_TRACE_ID']}-{os.environ['OTEL_SPAN_ID']}-01"}
ctx = TraceContextTextMapPropagator().extract(carrier=carrier)
with tracer.start_as_current_span("module2.main", context=ctx):
    ...
```

---

### 6. Semantic‑boundary rule

**Instrument only meaningful workflow steps**
✓ `generate_plan_structure` ✗ `parse_json`

---

### 7. Pitfalls & mitigations

1. **Dropping data under burst load** – batch processor set to 512 ([github.com][9])
2. **Failing to record errors** – use pattern § 5.1; span status = ERROR ([opentelemetry.io][4], [last9.io][10])
3. **Recursive logging loops** – use named loggers, filter SDK logs ([opentelemetry.io][2], [docs.smith.langchain.com][11])
4. **Fragmented traces** – propagate context (§ 5.2)
5. **Huge payloads** – truncation helper (§ 4), limits spec ([opentelemetry.io][12])
6. **Multiprocess exit** – SIGTERM flush helper (§ 4)
7. **Over‑instrumentation** – follow semantic‑boundary rule above.

---

### 8. Validation checklist

* Open Jaeger/Tempo: confirm one root span, nested child spans in logical order.
* Verify attributes: `gen_ai.system`, token counts, retry attempts show up.
* Trigger an intentional API failure: confirm ERROR status and `error.type`.
* Ensure `/data/1_01_plan_structure_<ts>.otlp.json` appears; open and validate against OTLP‑JSON schema.
* Load file into Lake Merritt; confirm ingestion (accepts OTLP‑JSON over file drop).

---

### 9. Collector file‑exporter snippet

```yaml
receivers:
  otlp: { protocols: { grpc: {} } }
exporters:
  file:
    path: "/data/1_01_plan_structure_%Y%m%dT%H%M%S.otlp.json"
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: []
      exporters: [file]
```

---

### 10. Anti‑leak notice

*Never* put secrets or full user data into span attributes; scrub or hash if needed.

---

### 11. Next step

Apply everything above to **`1_01_JSON_Goal_to_PlanStructure.py`**. When finished, ping me with:

* the updated file,
* a sample OTLP‑JSON snippet,
* a screenshot of the trace tree.

Thanks—looking forward to seeing your work!

*—Helper 1*

---

#### Sources

([opentelemetry.io][1], [opentelemetry.io][2], [opentelemetry.io][7], [github.com][9], [opentelemetry.io][8], [docs.smith.langchain.com][11], [opentelemetry.io][3], [opentelemetry.io][4], [last9.io][10], [opentelemetry.io][5], [opentelemetry.io][6], [opentelemetry.io][12])

[1]: https://opentelemetry.io/docs/specs/semconv/gen-ai/?utm_source=chatgpt.com "Semantic conventions for generative AI systems | OpenTelemetry"
[2]: https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/?utm_source=chatgpt.com "Gen AI | OpenTelemetry"
[3]: https://opentelemetry.io/docs/languages/python/instrumentation/?utm_source=chatgpt.com "Instrumentation - Python - OpenTelemetry"
[4]: https://opentelemetry.io/docs/specs/otel/trace/exceptions/?utm_source=chatgpt.com "Exceptions | OpenTelemetry"
[5]: https://opentelemetry.io/docs/specs/semconv/http/http-spans/?utm_source=chatgpt.com "Semantic conventions for HTTP spans | OpenTelemetry"
[6]: https://opentelemetry.io/docs/specs/semconv/database/database-spans/?utm_source=chatgpt.com "Semantic conventions for database client spans - OpenTelemetry"
[7]: https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-events/?utm_source=chatgpt.com "Semantic conventions for generative AI events - OpenTelemetry"
[8]: https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/?utm_source=chatgpt.com "Semantic conventions for generative client AI spans | OpenTelemetry"
[9]: https://github.com/Scale3-Labs/langtrace/discussions/71?utm_source=chatgpt.com "OpenTelemetry Trace Semantic Conventions for the LLM Stack #71"
[10]: https://last9.io/blog/opentelemetry-python-instrumentation/?utm_source=chatgpt.com "A Quick Guide for OpenTelemetry Python Instrumentation - Last9"
[11]: https://docs.smith.langchain.com/observability/how_to_guides/trace_with_opentelemetry?utm_source=chatgpt.com "Trace with OpenTelemetry | 🦜️🛠️ LangSmith - LangChain"
[12]: https://opentelemetry.io/docs/specs/otel/trace/sdk/?utm_source=chatgpt.com "Tracing SDK | OpenTelemetry"
