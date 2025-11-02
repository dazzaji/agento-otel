#!/usr/bin/env python3
"""
Agento   |  Module 1 — Goal → Plan Structure
================================================

Quick‑start (macOS / VS Code terminal)
--------------------------------------
# 0.  One‑time tools
brew install astral-sh/uv/uv      # or: pipx install uv
docker pull otel/opentelemetry-collector-contrib:0.127.0

# 1.  Virtual‑env + deps
rm -rf .venv
uv venv .venv && source .venv/bin/activate

uv pip install -r requirements.txt

or

uv pip compile requirements.in -o requirements-resolved.txt
uv pip install -r requirements-resolved.txt

# If UV still has issues, we can force newer OpenTelemetry versions:
uv pip install 'opentelemetry-api>=1.30.0' 'opentelemetry-sdk>=1.30.0' 'opentelemetry-exporter-otlp-proto-grpc>=1.30.0'

# 2. **DO FIRST TIME ONLY:**  Create local data directory and collector config (TRYING TO SKIP AS ONE-TIME - SEE IF THAT WORKS)
mkdir -p ./data
cat > collector-config.yaml << 'EOF'
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
exporters:
  file:
    path: "/data/agento_run_%Y%m%dT%H%M%S%f.otlp.json"
    rotation:
      max_megabytes: 100
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: []
      exporters: [file]
EOF


# 3.  A) **RUN DOCKER APP** B) Open **NEW TERMINAL and Start the collector**

docker rm -f agento-otel 2>/dev/null || true
docker run -d --name agento-otel \
  -v "$(pwd)/data":/data \
  -v "$(pwd)/collector-config.yaml":/etc/collector-config.yaml \
  -p 4317:4317 \
  otel/opentelemetry-collector-contrib:0.127.0 \
  --config=/etc/collector-config.yaml

# Now, go back to your original terminal where you activated the virtual environment

# 4.  Point OTEL client at the collector
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"

# 5.  Run this script - zsh (expected for Mac)
python 1_01-B_JSON_Goal_to_PlanStructure-OTEL-Semantic-OI.py

or if bash
python 1_01-B_JSON_Goal_to_PlanStructure-OTEL-Semantic-OI.py [project_goal.json]

"""


# ---------------------------------------------------------------------------
#  NEW CODE FOLLOWS
# ---------------------------------------------------------------------------


#!/usr/bin/env python3
"""
Agento | Module 1 — Goal → Plan Structure
=========================================

Quick‑start
-----------
brew install astral-sh/uv/uv                         # one‑time
docker pull otel/opentelemetry-collector-contrib:0.127.0

uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Start the collector (new terminal)

docker run -d --name agento-otel \
  -v "$(pwd)/data":/data \
  -v "$(pwd)/collector-config.yaml":/etc/collector-config.yaml \
  -p 4317:4317 \
  otel/opentelemetry-collector-contrib:0.127.0 \
  --config=/etc/collector-config.yaml

export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
python 1_01-B_JSON_Goal_to_PlanStructure-OTEL-Semantic-OI.py
"""


# ---------------------------------------------------------------------------
#  Standard Imports
# ---------------------------------------------------------------------------
import json
import logging
import os
import signal
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions, retry
from pydantic import BaseModel, Field, ValidationError

# ---------------------------------------------------------------------------
#  OpenTelemetry Setup
# ---------------------------------------------------------------------------
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from openinference.semconv.trace import OpenInferenceSpanKindValues as OIKind

MODULE_NAME = Path(__file__).stem
resource = Resource.create(
    {
        "service.name": "agento",
        "service.version": "1.0.0",
        "agento.module": MODULE_NAME,
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        "service.instance.id": str(uuid.uuid4()),
    }
)
provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
            insecure=True,
        ),
        max_export_batch_size=512,
    )
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

os.makedirs("data", exist_ok=True)

def exit_gracefully(signum=None, frame=None):
    span = trace.get_current_span()
    if span and span.is_recording() and span.status.status_code is StatusCode.UNSET:
        span.set_status(Status(StatusCode.OK))
        span.end()
    if trace.get_tracer_provider():
        trace.get_tracer_provider().force_flush()
    sys.exit(0)

signal.signal(signal.SIGTERM, exit_gracefully)

# ---------------------------------------------------------------------------
#  Console Helpers
# ---------------------------------------------------------------------------
def flush() -> None:
    sys.stdout.flush()

@contextmanager
def tee_output(filename: str | None = None):
    if filename is None:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{MODULE_NAME}_{ts}_output.log"
    proc = subprocess.Popen(
        ["tee", filename],
        stdin=subprocess.PIPE,
        stdout=sys.stdout,
        stderr=subprocess.STDOUT,
        text=True,
    )
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = proc.stdin
    try:
        yield
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        if proc.stdin:
            proc.stdin.close()
        proc.wait()

# ---------------------------------------------------------------------------
#  Environment & Gemini Configuration
# ---------------------------------------------------------------------------
load_dotenv()
gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    raise ValueError("GEMINI_API_KEY not found in environment variables")
genai.configure(api_key=gemini_api_key)

# ---------------------------------------------------------------------------
#  Pydantic Model
# ---------------------------------------------------------------------------
class PlanStructure(BaseModel):
    Title: str
    Overall_Summary: str
    Original_Goal: str
    Detailed_Outline: list[Dict[str, str]] = Field(..., description="List of steps")
    Evaluation_Criteria: Dict[str, str]
    Success_Measures: list[str]

# ---------------------------------------------------------------------------
#  OTEL Span Helpers
# ---------------------------------------------------------------------------
def set_ai_response_attribute(span, txt: str):
    span.set_attribute("gen_ai.response.content", txt)

def set_gemini_tokens(span, response):
    if hasattr(response, "usage_metadata"):
        span.set_attribute("gen_ai.usage.input_tokens", response.usage_metadata.prompt_token_count)
        span.set_attribute("gen_ai.usage.output_tokens", response.usage_metadata.candidates_token_count)

# ---------------------------------------------------------------------------
#  Business Logic
# ---------------------------------------------------------------------------
def generate_plan_structure(goal: str) -> Optional[Dict]:
    prompt = f"""
You are a top consultant called in to deliver a final version of what the user needs correctly, completely, and at high quality.
Create a comprehensive set of project deliverables, identifying each deliverable step by step, in JSON format to achieve the following goal: {goal}

The JSON should strictly adhere to this template:
{{
  "Title": "...",
  "Overall_Summary": "...",
  "Original_Goal": "{goal}",
  "Detailed_Outline": [
    {{"name": "Step 1", "content": "..."}},
    {{"name": "Step 2", "content": "..."}}
  ],
  "Evaluation_Criteria": {{
    "Step 1": "Criteria for Step 1",
    "Step 2": "Criteria for Step 2"
  }},
  "Success_Measures": ["...", "..."]
}}

Ensure that:
1. Each step in the "Detailed_Outline" has a corresponding entry in the "Evaluation_Criteria"
2. The Original_Goal field contains the exact goal provided
3. The response is valid JSON only, with no additional text or explanations
"""

    print("\nGenerating plan structure...", flush=True)

    model = genai.GenerativeModel(
        "gemini-2.5-pro",
        generation_config={"temperature": 0.1, "top_p": 1, "max_output_tokens": 8192},
    )

    @retry.Retry(
        predicate=retry.if_exception_type(
            google_exceptions.ServiceUnavailable, google_exceptions.Aborted, google_exceptions.InternalServerError
        ),
        initial=2.0, maximum=60.0, deadline=90,
    )
    def _call():
        return model.generate_content(prompt)

    with tracer.start_as_current_span(
        "llm.gemini.generate_plan",
        kind=SpanKind.CLIENT,
        attributes={
            # --- MODIFICATION START ---
            # Added standardized and custom attributes for rich, evaluatable traces.
            "openinference.span.kind": OIKind.LLM.value,
            "gen_ai.system": "gemini",
            "gen_ai.request.model": "gemini-1.5-pro",
            "gen_ai.request.temperature": 0.1,
            "gen_ai.operation.name": "chat",
            "agento.step_type": "plan",
            "agento.user_goal": goal,
            # --- MODIFICATION END ---
        },
    ) as span:
        try:
            response = _call()
            set_gemini_tokens(span, response)
            set_ai_response_attribute(span, response.text)
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise

    json_start = response.text.find("{")
    json_end = response.text.rfind("}") + 1
    try:
        plan_dict = json.loads(response.text[json_start:json_end])
        PlanStructure(**plan_dict)
    except (json.JSONDecodeError, ValidationError) as err:
        logging.error("Plan generation invalid: %s", err)
        return None

    return plan_dict

# ---------------------------------------------------------------------------
#  File Save Function
# ---------------------------------------------------------------------------
def save_plan_structure(plan: Dict, base_filename: str = "plan_structure") -> bool:
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        for filename in [f"{base_filename}.json", f"{base_filename}_{timestamp}.json"]:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(plan, f, indent=2, ensure_ascii=False)
            logging.info(f"Saved plan structure to {filename}")
        return True
    except Exception as e:
        logging.error(f"Error saving plan structure: {str(e)}")
        return False

# ---------------------------------------------------------------------------
#  Main Execution
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    with tee_output():
        with tracer.start_as_current_span(
            "agento.pipeline",
            kind=SpanKind.INTERNAL,
            attributes={"openinference.span.kind": OIKind.AGENT.value},
        ) as root_span:
            print("\nWelcome to the Project Plan Structure Generator!")
            flush()

            json_filename = sys.argv[1] if len(sys.argv) > 1 else "project_goal.json"
            try:
                with open(json_filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    project_goal = data.get("goal", "").strip()
                root_span.set_attribute("user_goal", project_goal)
            except (OSError, json.JSONDecodeError) as e:
                print(f"Error reading goal file: {e}")
                root_span.set_status(Status(StatusCode.ERROR, str(e)))
                return

            if not project_goal:
                print("Error: Project goal cannot be empty.")
                root_span.set_status(Status(StatusCode.ERROR, "empty goal"))
                return

            plan = generate_plan_structure(project_goal)
            if not plan:
                print("Error: Failed to generate plan structure.")
                root_span.set_status(Status(StatusCode.ERROR, "plan generation failed"))
                return

            plan["Original_Goal"] = project_goal

            print("\nGenerated Plan Structure:")
            print(json.dumps(plan, indent=2, ensure_ascii=False))
            flush()

            if save_plan_structure(plan):
                print("\nPlan structure successfully saved!")
            else:
                print("\nError: Failed to save plan structure.")

            carrier = {}
            propagate.inject(carrier)
            Path("trace.context").write_text(json.dumps(carrier))

    trace.get_tracer_provider().force_flush()

if __name__ == "__main__":
    main()