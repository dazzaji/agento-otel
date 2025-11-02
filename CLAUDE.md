# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
This is a Python-based AI testbench project called "Agento" that focuses on converting goals into structured plans using various AI models (OpenAI, Anthropic, Google Gemini). The project uses OpenTelemetry for observability and tracing of AI operations.

## Development Setup

### Virtual Environment
```bash
# Activate the virtual environment
source .venv/bin/activate  # On macOS/Linux
# or
.venv\Scripts\activate  # On Windows
```

### Dependencies
```bash
# Install dependencies
pip install -r requirements.txt
```

### Environment Variables
The project requires API keys in the `.env` file for:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GITHUB_TOKEN`
- `GEMINI_API_KEY`
- `PERPLEXITY_API_KEY`

### OpenTelemetry Setup
The project uses Docker for running an OpenTelemetry collector:
```bash
# Start the OpenTelemetry collector (runs on port 4317)
docker run -p 4317:4317 otel/opentelemetry-collector:latest
```

## Code Architecture

### Main Scripts
1. **1_01-B_JSON_Goal_to_PlanStructure-OTEL-Semantic-OI.py**: Converts JSON goals to structured plans using AI models with OpenTelemetry tracing
2. **1_06-B_Ingest-PlanStructure-to-Plan-OTEL-Semantic-OI-withComments.py**: Processes structured plans and adds detailed implementation comments
3. **2_Revise-Plan-Stable-OTEL.py**: Revises and refines existing plans

### Key Patterns
- All scripts use OpenTelemetry for tracing AI operations
- Scripts follow a naming convention: `{number}_{description}-OTEL-{features}.py`
- Each script includes detailed docstrings with setup instructions
- AI responses are tracked with semantic conventions for observability

### Data Flow
1. Goals are stored in `project_goal.json`
2. Scripts process goals through AI models to create structured plans
3. Results include detailed JSON structures with actions, timelines, and success metrics
4. All operations are traced through OpenTelemetry for monitoring

## Running Scripts
```bash
# Run individual scripts
python 1_01-B_JSON_Goal_to_PlanStructure-OTEL-Semantic-OI.py
python 1_06-B_Ingest-PlanStructure-to-Plan-OTEL-Semantic-OI-withComments.py
python 2_Revise-Plan-Stable-OTEL.py
```

## Project Structure
- Python scripts are in the root directory
- `data/` directory is available for storing outputs (currently empty)
- `.venv/` contains the Python 3.11 virtual environment
- `project_goal.json` contains the input goal data

## OpenTelemetry Implementation

> **⚠️ CRITICAL: DO NOT DAMAGE OR REMOVE OPENTELEMETRY INSTRUMENTATION**
> 
> This project extensively uses OpenTelemetry for observability. The telemetry implementation is a core feature that provides critical insights into AI operations. When making ANY code changes, ensure that:
> - All OpenTelemetry spans are preserved
> - Trace context propagation between modules remains intact
> - All attributes and semantic conventions continue to be recorded
> - The hierarchical span structure is maintained

### OpenTelemetry Architecture

The project implements comprehensive distributed tracing across all AI operations:

1. **Trace Structure**:
   - Root spans: `agento.pipeline`, `agento.pipeline.develop_plan`, `agento.pipeline.revise_plan`
   - LLM operation spans: `llm.{provider}.{operation}` (e.g., `llm.openai.develop_draft`)
   - Chain spans: `agento.chain.revise_step.{step_name}`
   - Event spans: `agento.event.{event_type}`

2. **Key Instrumentation Points**:
   - Every LLM call is wrapped in a span with detailed attributes
   - Token usage is tracked for all AI providers
   - Response content is recorded (truncated if >8192 chars)
   - Errors and exceptions are properly recorded

3. **Semantic Conventions**:
   - Uses OpenInference conventions (`openinference.span.kind`)
   - Follows OpenTelemetry GenAI semantic conventions
   - Custom `agento.*` attributes for domain-specific data

4. **Context Propagation**:
   - Trace context is saved to `trace.context` file between module executions
   - This enables a single distributed trace across all three scripts

5. **Critical Attributes Tracked**:
   - `gen_ai.system`, `gen_ai.request.model`, `gen_ai.request.temperature`
   - `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`
   - `agento.step_name`, `agento.step_type`, `agento.user_goal`
   - `agento.iterations_taken`, `agento.final_content`

### Maintaining OpenTelemetry When Coding

When modifying any script:
1. Preserve all `with tracer.start_as_current_span()` blocks
2. Maintain attribute recording patterns
3. Keep token tracking for LLM responses
4. Ensure proper span status setting (OK/ERROR)
5. Don't remove the OTLP exporter configuration
6. Test that traces are still being sent to the collector