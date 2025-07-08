# Memo: Finalized Dev Plan for Adding OpenTelemetry to Agento Modules

**To:** Agento Dev Team (Helper 2)
**From:** Architecture Review (Helper 1)
**Date:** 2025-07-08 (Revised)

Hello **Helper 2**,

We’re collaborating on **Agento**, a multi‑vendor LLM pipeline that produces project plans. My role (Helper 1) is to design observability; your role is to implement it. This memo contains the final, fact-checked requirements for this task.

#### **Mission, in one sentence**

Emit **standard OTLP traces** that (1) stream live to an OTEL Collector UI and (2) are archived as OTLP‑JSON files under `/data`, one per run, timestamped. Lake Merritt ingests plain OTLP, so if we stay within spec it will work automatically.

---

### **1. Copy‑paste setup block — start every module with this**

```python
# === OTEL SETUP | COPY AS-IS ===
import uuid, json, asyncio
import os
import signal
import sys
from opentelemetry import trace, propagate
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode, SpanKind as OTelSpanKind
from openinference.semconv.trace import OpenInferenceSpanKindValues as OIKind

resource = Resource.create({
    "service.name": "agento",
    "service.version": "1.0.0",
    "agento.module": "1_01_plan_structure",
    "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    "service.instance.id": str(uuid.uuid4())
})

provider = TracerProvider(resource=resource)
# Note: The OTLP Exporter must point to a collector endpoint.
# For local development, this can be a Docker container.
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")),
        max_export_batch_size=512
    )
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)
# === END SETUP BLOCK ===
```

*(Update `agento.module` per file.)*

**Success Criterion:** After adding this block to a script, run it. The script should execute without `NameError` or `ImportError`. If an OTel collector is running, no connection errors should appear in the script's logs.

---

### **2. Why we instrument**

The telemetry **is the product**: it must capture every meaningful decision, prompt, response, retry, error and guard‑rail so later analyses can judge goal‑fitness, agent behaviour and performance.
*   **Root AGENT span MUST include** `user_goal`, `agent.final_response`, `expected_response` (if available) for external evaluation pipelines.
*   **Tag that root span with** `openinference.span.kind = "AGENT"` so Lake Merritt recognises it as the evaluation item.

**Success Criterion:** A test that inspects the generated trace JSON should confirm the root span (the one with no `parent_span_id`) has the `openinference.span.kind` attribute set to `AGENT` and contains the `user_goal` attribute.

---

### **3. Gen‑AI semantic conventions — quick reference**

**A Note on Standards:** The following table combines two sets of standards. The **OpenTelemetry Semantic Conventions (`gen_ai.*`)** provide the base attributes for interoperability with any OTel tool. We are layering on one specific attribute from the **OpenInference Semantic Conventions (`openinference.span.kind`)**, because this `span.kind` is what the Lake Merritt evaluation platform uses for its powerful filtering capabilities.

| Attribute | When to set | Example |
| :--- | :--- | :--- |
| `gen_ai.system` | Every LLM span | `"gemini"` |
| `gen_ai.request.model` | Input | `"gemini-1.5-pro"` |
| `gen_ai.response.model` | Output | `"gemini-1.5-pro"` |
| `gen_ai.operation.name` | Every LLM span | `"chat"` |
| `gen_ai.request.temperature`| Input | `0.1` *(range 0 – 2)* |
| `gen_ai.usage.input_tokens` | After response | `prompt_token_count` |
| `gen_ai.usage.output_tokens`| After response | `completion_token_count` |
| `gen_ai.response.finish_reasons`| After response | `["stop"]` |

See spec for full list ([opentelemetry.io][2], [opentelemetry.io][8]).

---

### **4. Helpers**

```python
# truncate big responses
def set_ai_response_attribute(span, txt:str):
    # OTel spec allows redaction; see gen-ai-events section.
    if len(txt) > 8192:
        span.set_attribute("gen_ai.response.truncated", True)
        span.set_attribute("gen_ai.response.truncated_reason", "size_limit")
        span.set_attribute("gen_ai.response.length", len(txt))
        span.set_attribute("gen_ai.response.content", txt[:8000] + "...[truncated]")
    else:
        span.set_attribute("gen_ai.response.content", txt)

# graceful shutdown for multiproc / SIGTERM
def exit_gracefully(signum=None, frame=None):
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_status(Status(StatusCode.OK))
        span.end()
    # Force flush any buffered spans.
    trace.get_tracer_provider().force_flush()
    sys.exit(0)

signal.signal(signal.SIGTERM, exit_gracefully)
```

---

### **5. Instrumentation patterns**

#### **5.1 LLM call with retries**

**Success Criterion:** Write a unit test that forces this function to fail once before succeeding. The final trace JSON for this operation should show two `attempt_*` spans, where the first has a status of `ERROR` and an `exception` event, and the second has a status of `OK`.

```python
with tracer.start_as_current_span(
    "llm.gemini.generate_with_retry",
    kind=OTelSpanKind.CLIENT, # External LLM calls are CLIENT spans
    attributes={"openinference.span.kind": OIKind.LLM.value}
) as retry_span:
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
                # Set correct, non-deprecated token attributes
                if hasattr(response, 'usage'):
                    attempt_span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
                    attempt_span.set_attribute("gen_ai.usage.output_tokens", response.usage.completion_tokens)
                attempt_span.set_status(Status(StatusCode.OK))
                break
            except Exception as e:
                attempt_span.record_exception(e)
                attempt_span.set_status(Status(StatusCode.ERROR, str(e)))
                attempt_span.set_attribute("error.type", type(e).__name__)
                if attempt + 1 == max_retries:
                    retry_span.set_status(Status(StatusCode.ERROR, "All retry attempts failed."))
                    raise
                await asyncio.sleep(backoff(attempt))
```

#### **5.2 Context propagation between modules**

**Success Criterion:** After implementing, run the first two Agento modules (`1_01_...` and `1_05_...`). Inspect the generated OTLP JSON file. The `parent_span_id` of the main span in the second module must match the `span_id` of the main span from the first module.

```python
# === In the sending module (e.g., end of 1_01_JSON_Goal_to_PlanStructure.py) ===
# Create a carrier dictionary to hold the context
carrier = {}
# Use the standard propagator to inject the current span's context into the carrier
propagate.inject(carrier)
# Save the context to a file that the next process can read.
with open("trace.context", "w") as f:
    json.dump(carrier, f)

# === In the receiving module (e.g., start of 1_05_Ingest-PlanStructure-to-Plan.py) ===
# Load the context from the file
try:
    with open("trace.context", "r") as f:
        carrier = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    carrier = {}
# Use the standard propagator to extract the context
ctx = propagate.extract(carrier)
# Start the new span as a child of the propagated context
with tracer.start_as_current_span("module2.main", context=ctx) as span:
    # ... rest of the module's logic ...
```

---

### **6. Semantic‑boundary rule**

**Instrument only meaningful workflow steps**
✓ `generate_plan_structure` ✗ `parse_json`

---

### **7. Pitfalls & mitigations**

1.  **Dropping data under burst load** – batch processor set to 512 ([github.com][9])
2.  **Failing to record errors** – use pattern § 5.1; span status = ERROR ([opentelemetry.io][4], [last9.io][10])
3.  **Recursive logging loops** – use named loggers, filter SDK logs ([opentelemetry.io][2], [docs.smith.langchain.com][11])
4.  **Fragmented traces** – propagate context (§ 5.2)
5.  **Huge payloads** – truncation helper (§ 4), limits spec ([opentelemetry.io][12])
6.  **Multiprocess exit** – SIGTERM flush helper (§ 4)
7.  **Over‑instrumentation** – follow semantic‑boundary rule above.

---

### **8. Validation checklist**

*   Open Jaeger/Tempo: confirm one root span, nested child spans in logical order.
*   Verify attributes: `gen_ai.system`, `gen_ai.request.model`, retry attempts show up.
*   Trigger an intentional API failure: confirm ERROR status and `error.type`.
*   Ensure `/data/1_01_plan_structure_<ts>.otlp.json` appears; open and validate against OTLP‑JSON schema.
*   Load file into Lake Merritt; confirm ingestion (accepts OTLP‑JSON over file drop).
*   Confirm `openinference.span.kind` present on all spans.
*   Confirm `input_tokens`/`output_tokens` attributes present.

---

### **9. Collector file‑exporter snippet**

```yaml
receivers:
  otlp: { protocols: { grpc: {} } }
exporters:
  file:
    path: "/data/agento_run_%Y%m%dT%H%M%S.otlp.json"
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: []
      exporters: [file]
```
*Use the `otel/opentelemetry-collector-contrib:0.127.0` or newer docker image for the `file` exporter; the core collector does not include it.*

---

### **10. Anti‑leak notice**

*Never* put secrets or full user data into span attributes; scrub or hash if needed.

---

### **11. Next step**

Apply everything above to **`1_01_JSON_Goal_to_PlanStructure.py`**. When finished, ping me with:

*   The updated Python file.
*   A sample OTLP‑JSON snippet generated by the updated script.
*   **Overall Success Criterion:** A final test where you run all three Agento modules in sequence. The resulting single OTLP JSON file, when loaded into Lake Merritt with an appropriate Eval Pack, should successfully ingest and produce an evaluation result without errors.

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
