# Memo: Finalized Dev Plan for Adding OpenTelemetry to Agento Modules

**To:** Agento Dev Team (Helper 2)  
**From:** Architecture Review (Helper 1)  
**Date:** 2025-07-08 (Revised v4)

UPDATE BEFORE STARTING IMPLEMENTATION OF BELOW PLAN: SEE: [https://github.com/dazzaji/agento-otel/issues/2](https://github.com/dazzaji/agento-otel/issues/2)

Hello **Helper 2**,

We're collaborating on **Agento**, a multi‑vendor LLM pipeline that produces project plans. My role (Helper 1) is to design observability; your role is to implement it. This memo contains the final, technically-vetted requirements addressing all identified issues.

#### **Mission, in one sentence**

Emit **standard OTLP traces** that (1) stream live to an OTEL Collector UI and (2) are archived as OTLP‑JSON files under `/data`, one file per complete pipeline run, timestamped with microsecond precision.

---

### **Prerequisites**

Before starting, ensure these dependencies are installed:
```bash
pip install opentelemetry-api>=1.24
pip install opentelemetry-sdk>=1.24
pip install opentelemetry-exporter-otlp-proto-grpc>=1.24
pip install openinference-semantic-conventions>=0.1.15
```

Create the data directory:
```bash
mkdir -p ./data
```

---

### **1. Copy‑paste setup block — start every module with this**

```python
# === OTEL SETUP | COPY AS-IS ===
import os, uuid, signal, sys, time, json
from pathlib import Path
from opentelemetry import trace, propagate
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode, SpanKind
from openinference.semconv.trace import OpenInferenceSpanKindValues as OIKind

# Module-specific resource configuration - automatically derived from filename
module_name = Path(__file__).stem
resource = Resource.create({
    "service.name": "agento",
    "service.version": "1.0.0",
    "agento.module": module_name,
    "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    "service.instance.id": str(uuid.uuid4())
})

provider = TracerProvider(resource=resource)
# NOTE: endpoint accepts schemes for gRPC exporter
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
            insecure=True  # Required for local development without TLS
        ),
        max_export_batch_size=512
    )
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Ensure /data directory exists
os.makedirs("/data", exist_ok=True)
# === END SETUP BLOCK ===
```

**Success Criterion:** After adding this block to a script, run it. The script should execute without `NameError` or `ImportError`. If an OTel collector is running, no connection errors should appear in the script's logs.

---

### **2. Trace Structure and Why We Instrument**

The telemetry **is the product**: it must capture every meaningful decision, prompt, response, retry, error and guard‑rail so later analyses can judge goal‑fitness, agent behaviour and performance.

**Trace Architecture:**
- **Pipeline Root Span**: Created by the orchestrator (e.g., `run_all.sh` wrapper) OR by the first module
- **Module Spans**: Each module creates a child span of the propagated context
- **LLM Spans**: Each LLM call creates a child span with vendor-specific attributes

**Required Attributes:**
- The first module's span MUST include `user_goal` and be tagged with `openinference.span.kind = OIKind.AGENT.value`
- The final module's span SHOULD include `agent.final_response` attribute
- Add `expected_response` if available for external evaluation pipelines

**Success Criterion:** A test that inspects the generated trace JSON should confirm:
- A single root span (no `parent_span_id`) exists
- The first module's span has `openinference.span.kind` set to `"AGENT"` and contains `user_goal`
- All subsequent module spans have `parent_span_id` pointing up the trace tree

---

### **3. Gen‑AI Semantic Conventions — Quick Reference**

**A Note on Standards:** We combine OpenTelemetry Semantic Conventions (`gen_ai.*`) with OpenInference Semantic Conventions (`openinference.span.kind`) for Lake Merritt compatibility.

| Attribute | When to set | Example |
| :--- | :--- | :--- |
| `gen_ai.system` | Every LLM span | `"gemini"`, `"openai"`, `"anthropic"` |
| `gen_ai.request.model` | Input | `"gemini-1.5-pro"`, `"gpt-4"`, `"claude-3-5-sonnet"` |
| `gen_ai.response.model` | Output | Same as request model |
| `gen_ai.operation.name` | Every LLM span | `"chat"` |
| `gen_ai.request.temperature`| Input | `0.1` *(range 0 – 2)* |
| `gen_ai.usage.input_tokens` | After response | See vendor-specific helpers below |
| `gen_ai.usage.output_tokens`| After response | See vendor-specific helpers below |
| `gen_ai.response.finish_reason`| After response | `["stop"]` *(Note: plural)* |
| `openinference.span.kind` | Span type | `OIKind.AGENT.value`, `OIKind.LLM.value`, `OIKind.CHAIN.value`, `OIKind.TOOL.value` |

---

### **4. Helper Functions**

```python
# Truncate big responses
def set_ai_response_attribute(span, txt: str):
    if len(txt) > 8192:
        span.set_attribute("gen_ai.response.truncated", True)
        # Custom Agento extension; not part of the official spec
        span.set_attribute("agento.response.truncated_reason", "size_limit")
        span.set_attribute("agento.response.length", len(txt))
        span.set_attribute("gen_ai.response.content", txt[:8000] + "...[truncated]")
    else:
        span.set_attribute("gen_ai.response.content", txt)

# Graceful shutdown for multiproc / SIGTERM
def exit_gracefully(signum=None, frame=None):
    span = trace.get_current_span()
    if span and span.is_recording():
        # Only set OK status if status is UNSET
        if span.status.status_code is StatusCode.UNSET:
            span.set_status(Status(StatusCode.OK))
        span.end()
    # Force flush any buffered spans
    if trace.get_tracer_provider():
        trace.get_tracer_provider().force_flush()
    sys.exit(0)

signal.signal(signal.SIGTERM, exit_gracefully)

# Vendor-specific token extraction helpers
def set_gemini_tokens(span, response):
    """Extract and set token counts from Gemini response"""
    if hasattr(response, 'usage_metadata'):
        span.set_attribute("gen_ai.usage.input_tokens", 
                         response.usage_metadata.prompt_token_count)
        span.set_attribute("gen_ai.usage.output_tokens", 
                         response.usage_metadata.candidates_token_count)

def set_openai_tokens(span, response):
    """Extract and set token counts from OpenAI response"""
    if hasattr(response, 'usage'):
        span.set_attribute("gen_ai.usage.input_tokens", 
                         response.usage.prompt_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", 
                         response.usage.completion_tokens)

def set_anthropic_tokens(span, response):
    """Extract and set token counts from Anthropic response"""
    if hasattr(response, 'usage'):
        span.set_attribute("gen_ai.usage.input_tokens", 
                         response.usage.input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", 
                         response.usage.output_tokens)

# Exponential backoff helper
def exponential_backoff(attempt: int) -> float:
    """Calculate exponential backoff with jitter"""
    import random
    return min(60, (2 ** attempt) + (random.random() * 0.1))
```

---

### **5. Instrumentation Patterns (Synchronous)**

#### **5.1 LLM Call Instrumentation Options**

Since the existing code uses `@retry.Retry` decorators, we have two implementation options:

**Option A: Keep decorators, wrap with single span (Minimal change)**
```python
# Keep existing retry decorator
@retry.Retry(...)
def generate_with_retry():
    return model.generate_content(prompt)

with tracer.start_as_current_span(
    "llm.gemini.generate",
    kind=SpanKind.CLIENT,
    attributes={
        "openinference.span.kind": OIKind.LLM.value,
        "gen_ai.system": "gemini",
        "gen_ai.request.model": "gemini-1.5-pro",
        "gen_ai.request.temperature": 0.1
    }
) as span:
    try:
        response = generate_with_retry()
        set_ai_response_attribute(span, response.text)
        set_gemini_tokens(span, response)
        span.set_attribute("gen_ai.response.model", "gemini-1.5-pro")
        span.set_status(Status(StatusCode.OK))
    except Exception as e:
        span.record_exception(e)
        span.set_status(Status(StatusCode.ERROR, str(e)))
        raise
```

**Option B: Replace decorator with manual retry (Recommended for visibility)**
```python
# Remove @retry.Retry decorator and implement manual retry with spans
with tracer.start_as_current_span(
    "llm.gemini.generate_with_retry",
    kind=SpanKind.CLIENT,
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
                response = model.generate_content(prompt)
                set_ai_response_attribute(attempt_span, response.text)
                set_gemini_tokens(attempt_span, response)
                attempt_span.set_attribute("gen_ai.response.model", "gemini-1.5-pro")
                attempt_span.set_status(Status(StatusCode.OK))
                break
            except Exception as e:
                attempt_span.record_exception(e)
                attempt_span.set_status(Status(StatusCode.ERROR, str(e)))
                attempt_span.set_attribute("error.type", type(e).__name__)
                if attempt + 1 == max_retries:
                    retry_span.set_status(Status(StatusCode.ERROR, "All retry attempts failed"))
                    raise
                time.sleep(exponential_backoff(attempt))
```

**Success Criterion:** Write a unit test that forces this function to fail once before succeeding. The final trace JSON should show two `attempt_*` spans, where the first has status `ERROR` and the second has status `OK`.

#### **5.2 Context Propagation Between All Modules**

```python
# === At the END of module 1_01 ===
carrier = {}
propagate.inject(carrier)
Path("trace.context").write_text(json.dumps(carrier))

# === At the START of module 1_05 ===
try:
    carrier = json.loads(Path("trace.context").read_text())
except (FileNotFoundError, json.JSONDecodeError):
    carrier = {}
ctx = propagate.extract(carrier)

# Determine if this is the first module
is_first_module = (module_name == "1_01_JSON_Goal_to_PlanStructure")
span_name = "agento.pipeline" if is_first_module else f"agento.module.{module_name}"

with tracer.start_as_current_span(span_name, context=ctx) as main_span:
    if is_first_module:
        main_span.set_attribute("openinference.span.kind", OIKind.AGENT.value)
        main_span.set_attribute("user_goal", project_goal)
    
    # ... module logic ...
    
    # At the END, save context for next module
    carrier = {}
    propagate.inject(carrier)
    Path("trace.context").write_text(json.dumps(carrier))

# === At the START of module 2_Revise ===
try:
    carrier = json.loads(Path("trace.context").read_text())
except (FileNotFoundError, json.JSONDecodeError):
    carrier = {}
ctx = propagate.extract(carrier)

with tracer.start_as_current_span(f"agento.module.{module_name}", context=ctx) as main_span:
    # ... module logic ...
    
    # If this is the final module, add the final response
    if further_revised_plan:
        main_span.set_attribute("agent.final_response", json.dumps(further_revised_plan))
```

**Success Criterion:** After implementing, run all three Agento modules in sequence. Inspect the generated OTLP JSON file. The trace should show a single connected tree with proper parent-child relationships.

---

### **6. Semantic‑Boundary Rule**

**Instrument only meaningful workflow steps:**

✓ High-level operations:
- `generate_plan_structure`
- `develop_drafts` 
- `revise_step_with_llms`

✗ Low-level utilities:
- `parse_json`
- `save_file`
- `tee_output`

---

### **7. Pitfalls & Mitigations**

1. **Dropping data under burst load** – batch processor set to 512
2. **Failing to record errors** – use pattern § 5.1; span status = ERROR
3. **Recursive logging loops** – use named loggers, filter SDK logs
4. **Fragmented traces** – propagate context between all modules (§ 5.2)
5. **Huge payloads** – truncation helper (§ 4)
6. **Multiprocess exit** – SIGTERM flush helper (§ 4)
7. **Over‑instrumentation** – follow semantic‑boundary rule above

---

### **8. Validation Checklist**

- Open Jaeger/Tempo: confirm one connected trace spanning all three modules
- Verify attributes: `gen_ai.system`, `gen_ai.request.model`, token counts present
- Trigger an intentional API failure: confirm ERROR status and `error.type`
- Ensure `/data/agento_run_<timestamp>.otlp.json` appears after full pipeline run
- Load file into Lake Merritt; confirm ingestion
- Confirm `openinference.span.kind` present on relevant spans using enum values
- Verify first module sets `"AGENT"` kind and includes `user_goal`

---

### **9. Collector File‑Exporter Configuration**

```yaml
receivers:
  otlp: 
    protocols: 
      grpc: 
        endpoint: 0.0.0.0:4317
exporters:
  file:
    path: "/data/agento_run_%Y%m%dT%H%M%S%f.otlp.json"  # %f for microseconds
    rotation:
      max_megabytes: 100
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: []
      exporters: [file]
```

Use the `otel/opentelemetry-collector-contrib:0.127.0` or newer docker image (the core collector does not include the file exporter).

To run the collector with proper volume mounting:
```bash
docker run -v $(pwd)/data:/data -v $(pwd)/collector-config.yaml:/etc/collector-config.yaml \
  -p 4317:4317 \
  otel/opentelemetry-collector-contrib:0.127.0 \
  --config=/etc/collector-config.yaml
```

---

### **10. Anti‑Leak Notice**

*Never* put secrets or full user data into span attributes; scrub or hash if needed.

---

### **11. Module-Specific Implementation Examples**

**1_01_JSON_Goal_to_PlanStructure.py:**
```python
# After the setup block and before main logic
is_first_module = True
ctx = None  # No parent context for first module

with tracer.start_as_current_span(
    "agento.pipeline",
    kind=SpanKind.INTERNAL,
    attributes={
        "openinference.span.kind": OIKind.AGENT.value,
        "user_goal": project_goal
    }
) as span:
    # Wrap the generate_plan_structure call
    with tracer.start_as_current_span(
        "generate_plan_structure",
        kind=SpanKind.INTERNAL
    ) as gen_span:
        plan = generate_plan_structure(project_goal)
    
    # ... rest of main() logic ...
    
    # At the end, save context for next module
    carrier = {}
    propagate.inject(carrier)
    Path("trace.context").write_text(json.dumps(carrier))
```

**1_05_Ingest-PlanStructure-to-Plan.py:**
```python
# Load context from previous module
try:
    carrier = json.loads(Path("trace.context").read_text())
except (FileNotFoundError, json.JSONDecodeError):
    carrier = {}
ctx = propagate.extract(carrier)

with tracer.start_as_current_span(
    "agento.module.ingest_plan", 
    context=ctx,
    kind=SpanKind.INTERNAL,
    attributes={"openinference.span.kind": OIKind.CHAIN.value}
) as span:
    # Instrument major operations
    with tracer.start_as_current_span("develop_drafts"):
        drafts = develop_drafts(plan, original_goal)
    
    with tracer.start_as_current_span("generate_revision_requests"):
        revision_requests = generate_revision_requests(drafts, plan, original_goal)
    
    # ... rest of logic ...
    
    # Save context for next module
    carrier = {}
    propagate.inject(carrier)
    Path("trace.context").write_text(json.dumps(carrier))
```

**2_Revise-Plan-Stable.py:**
```python
# Load context from previous module
try:
    carrier = json.loads(Path("trace.context").read_text())
except (FileNotFoundError, json.JSONDecodeError):
    carrier = {}
ctx = propagate.extract(carrier)

with tracer.start_as_current_span(
    "agento.module.revise_plan",
    context=ctx, 
    kind=SpanKind.INTERNAL,
    attributes={"openinference.span.kind": OIKind.CHAIN.value}
) as span:
    # Instrument the revision process
    with tracer.start_as_current_span("further_revise_plan"):
        further_revised_plan, revision_contexts = further_revise_plan(
            revised_plan,
            anthropic_client,
            genai.GenerativeModel(GEMINI_MODEL),
            verbose=True
        )
    
    # Add final response as attribute
    if further_revised_plan:
        span.set_attribute("agent.final_response", json.dumps(further_revised_plan))
```

---

### **12. Next Steps**

Apply everything above to all three modules. When finished, provide:

1. The updated Python files
2. A sample OTLP‑JSON file from a complete run
3. Confirmation that all three modules produce a single connected trace

**Overall Success Criterion:** Run all three Agento modules in sequence. The resulting single OTLP JSON file should show a connected trace tree. When loaded into Lake Merritt with an appropriate Eval Pack, it should successfully ingest and produce evaluation results without errors.

Thanks—looking forward to seeing your implementation!

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
