# RUN: python 1_06-B_Ingest-PlanStructure-to-Plan-OTEL-Semantic-OI-withComments.py

# This code ingests the plan_structure.json from either 1_JSON_Goal_to_PlanStructure.py or from
# 1_UserInputGoal-to-ExportPlanStructure_MoreLogging-RigorousPrompt.py and exports full plan w/revision requests
# RUN: python3 1_06-B_Ingest-PlanStructure-to-Plan-OTEL-Semantic-OI-withComments.py

# Note: Before running this script, ensure you have installed the dependencies and required libraries:
# python -m pip install -U openai google-generativeai python-dotenv tqdm pydantic
# python -m pip install -r requirements.txt

# Possible goal: I need to create a new python script to set up creative collaborative LLM-based agents design that uses OpenAI, Anthropic, and Google APIs for their most powerful LLMs in a way that the agents chat with each other to work on achieving a goal that the user inputs. The system brainstorms potential approaches and chooses the best (based on criteria the LLM's generate in the context of the goal) and then develops a plan, then implements and refines the plan until it is finalized. Then the plan guides generation, refinements, and finalization of the final output. Each stage of development is created, refined, and agreed as finished by the 3 agents and is then exported as JSON and Markdown for the user to have.

import openai
from tqdm import tqdm
import os
from dotenv import load_dotenv
import json
import sys
import subprocess  # Added for tee'd logging
from typing import List, Dict, Optional
from pydantic import BaseModel, Field, ValidationError
from datetime import datetime
import logging
import google.generativeai as genai
import typing_extensions as typing
from google.api_core import retry
from google.api_core import exceptions as google_exceptions
import time # Added for rate limiting
import contextlib # Added for tee'd logging

# ---------------------------------------------------------------------------
#  OpenTelemetry Imports and Setup
# ---------------------------------------------------------------------------
import signal
import uuid
from pathlib import Path
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

# Graceful shutdown — flush spans and end open span
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
#  Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ---------------------------------------------------------------------------
#  Console helpers (unchanged)
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def tee_output(filename=None):
    """
    Context manager that captures all stdout/stderr and writes it to a file while
    still displaying it in the terminal. Automatically generates timestamped filename
    if none provided.
    """
    if filename is None:
        # Get script name without extension
        script_name = os.path.splitext(os.path.basename(__file__))[0]
        # Create timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Combine for filename
        filename = f"{script_name}_{timestamp}_output.log"
    try:
        # Open the tee process - using universal_newlines for text mode
        process = subprocess.Popen(
            ['tee', filename],
            stdin=subprocess.PIPE,
            stdout=sys.stdout,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        # Save original stdout/stderr
        old_stdout, old_stderr = sys.stdout, sys.stderr
        # Replace stdout/stderr with tee's stdin
        sys.stdout = sys.stderr = process.stdin
        yield
    finally:
        # Restore original stdout/stderr
        sys.stdout, sys.stderr = old_stdout, old_stderr
        # Close tee's stdin and wait for it to finish
        if process.stdin:
            process.stdin.close()
        process.wait()

# ---------------------------------------------------------------------------
#  Environment and API Client Setup
# ---------------------------------------------------------------------------
# Load environment variables
load_dotenv()

# Function to flush the stdout buffer
def flush():
    sys.stdout.flush()

# Access API keys from environment variables
openai_api_key = os.getenv("OPENAI_API_KEY")
gemini_api_key = os.getenv("GEMINI_API_KEY")

# Initialize clients
openai_client = openai.OpenAI(api_key=openai_api_key)
genai.configure(api_key=gemini_api_key)

# Verbose mode
verbose = True

# ---------------------------------------------------------------------------
#  Pydantic model for the standardized schema
# ---------------------------------------------------------------------------
class ProjectPlan(BaseModel):
    """
    This model ensures that the project plan adheres to a consistent structure.
    It facilitates data validation and serialization/deserialization.
    """
    Original_Goal: str
    Title: str
    Overall_Summary: str
    Detailed_Outline: List[Dict[str, str]] = Field(..., description="List of steps with content")
    Evaluation_Criteria: Dict[str, str] = Field(..., description="Criteria for evaluating each step")
    revision_requests: Optional[Dict[str, str]] = Field(None, description="Revision suggestions for each step")
    Success_Measures: List[str]

# ---------------------------------------------------------------------------
#  Helper functions for spans
# ---------------------------------------------------------------------------
def set_ai_response_attribute(span, txt: str):
    if len(txt) > 8192:
        span.set_attribute("gen_ai.response.truncated", True)
        span.set_attribute("agento.response.truncated_reason", "size_limit")
        span.set_attribute("agento.response.length", len(txt))
        txt = txt[:8000] + "...[truncated]"
    span.set_attribute("gen_ai.response.content", txt)

def set_openai_tokens(span, response):
    if hasattr(response, "usage") and response.usage:
        span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", response.usage.completion_tokens)

# ---------------------------------------------------------------------------
#  Function to load plan structure from file
# ---------------------------------------------------------------------------
def load_plan_structure(filename: str = "plan_structure.json") -> Optional[Dict]:
    """
    Loads and validates the initial plan structure from a JSON file using the ProjectPlan model.
    """
    try:
        print(f"Loading plan structure from {filename}...")
        with open(filename, "r") as f:
            raw_plan = json.load(f)
        # Validate the plan structure using ProjectPlan model
        try:
            validated_plan = ProjectPlan(**raw_plan)
            plan_dict = validated_plan.model_dump()
            print("Successfully loaded and validated plan structure")
            if verbose:
                print("Loaded plan structure:")
                print(json.dumps(plan_dict, indent=2))
            return plan_dict
        except ValidationError as e:
            logging.error(f"Error: Invalid plan structure in {filename}: {e}")
            return None
    except FileNotFoundError:
        logging.error(f"Error: Could not find {filename}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Error: Invalid JSON in {filename}: {e}")
        return None

# ---------------------------------------------------------------------------
#  Function to develop drafts (using GPT-4)
# ---------------------------------------------------------------------------
def develop_drafts(plan: Optional[Dict], goal: str) -> Dict[str, str]:
    """
    For each step in the plan, uses GPT-4 (OpenAI) to generate a full draft deliverable.
    Returns a dictionary mapping step names to draft content.
    """
    if plan is None:
        logging.error("No plan available. Cannot develop drafts.")
        flush()
        return {}

    drafts = {}

    # Rate limiting parameters for OpenAI API
    requests_per_minute = 300 # Conservative estimate for GPT-4, adjust if needed
    delay_in_seconds = 60.0 / requests_per_minute

    if "Detailed_Outline" in plan and "Evaluation_Criteria" in plan:
        for step_item in tqdm(plan["Detailed_Outline"], desc="Developing drafts..."):
            step = step_item["name"]

            criteria = plan["Evaluation_Criteria"].get(step, "")

            prompt = f"CONTEXT: You are a top consultant called in to deliver a final version of the deliverable for this step of the project. Develop a full draft for the following deliverable for this step in the project: {step}\n"
            prompt += f"CONTEXT: Silently consider to yourself the following evaluation criteria before you decide on and provide the deliverable for this step of the project: {criteria}\n"
            prompt += f"CONTEXT: Silently consider to yourself the following broader context before you decide on and provide the deliverable for this step of the project: {json.dumps(plan)}\n"
            prompt += f"CONTEXT: Silently consider to yourself the following user goal for this work to ensure your work on this part is well aligned to achieve the goal and do this before you decide on and provide the deliverable for this step of the project: {goal}\n"
            prompt += "YOUR INSTRUCTION: Given all this information, now write a comprehensive and well-structured deliverable that achieves the user goal for this step of the project and is well aligned with the evaluation criteria but do not restate the evaluation criteria."

            if verbose:
                print(f"\nPrompt for step '{step}':\n{prompt}\n")
                flush()

            # OTEL: Add a span for each LLM call
            with tracer.start_as_current_span(
                f"llm.openai.develop_draft.{step.replace(' ', '_')}",
                kind=SpanKind.CLIENT,
                attributes={
                    ### MODIFICATION ###
                    # Added standardized and custom attributes for rich, evaluatable traces.
                    "openinference.span.kind": OIKind.LLM.value,
                    "gen_ai.system": "openai",
                    "gen_ai.request.model": "gpt-4",
                    "gen_ai.operation.name": "chat",
                    "agento.step_type": "draft",
                    "agento.step_name": step,
                    "agento.instructions": prompt,
                    "agento.criteria": criteria,
                    "agento.user_goal": goal,
                    ### END MODIFICATION ###
                },
            ) as span:
                try:
                    response = openai_client.chat.completions.create(
                        model="gpt-4",
                        messages=[{"role": "user", "content": prompt}]
                    )
                    set_openai_tokens(span, response)
                    draft_content = response.choices[0].message.content
                    set_ai_response_attribute(span, draft_content)
                    span.set_status(Status(StatusCode.OK))

                    drafts[step] = draft_content

                    if verbose:
                        print(f"Draft for step '{step}':\n{draft_content}\n")
                        flush()

                    # Introduce a delay to respect rate limits
                    time.sleep(delay_in_seconds)

                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    logging.error(f"Error calling OpenAI API for step '{step}': {e}")
                    flush()
    else:
        logging.error("'Detailed_Outline' or 'Evaluation_Criteria' missing in the plan. Cannot generate drafts.")
        flush()
    return drafts

# ---------------------------------------------------------------------------
#  Function to generate revision requests
# ---------------------------------------------------------------------------
def generate_revision_requests(drafts, plan, original_goal):
    """
    For each draft, uses GPT-4 to generate revision requests.
    Returns a dictionary mapping step names to revision request content.
    """
    revision_requests = {}
    if not drafts or not plan:
        print("Error: No drafts or plan to process.")
        flush()
        return revision_requests

    # Rate limiting parameters for OpenAI API
    requests_per_minute = 300 # Conservative estimate for GPT-4, adjust if needed
    delay_in_seconds = 60.0 / requests_per_minute

    for step, draft in tqdm(drafts.items(), desc="Generating revision requests..."):
        ### MODIFICATION ###
        # This one-line addition is a bug fix to ensure the correct context (the step's
        # instructions) is available for the OTEL span attributes below.
        current_plan_item_content = next((item["content"] for item in plan.get("Detailed_Outline", []) if item["name"] == step), "Instructions not found")
        ### END MODIFICATION ###
        
        prompt = f"""CONTEXT: You are an experienced professional evaluator who prizes practical, actionable feedback and advice and who provides clear and high quality focused outputs that follow instructions to the letter. You will evaluate the following specific draft content for a "step" in a broader project and provide me with your recommended revisions in order for the draft to better achieve the user's goal for this part of the overall work. First I will provide you further context and then a more specific instruction:

CONTEXT: THIS IS THE SPECIFIC CONTENT YOU ARE TO EVALUATE AND RECOMMEND REVISIONS FOR: {step}:
{draft}

CONTEXT: Silently consider to yourself the following user goal for this work to ensure your work on this part is well aligned to achieve the goal and do this before you decide on and provide your recommendations for revisions to the draft for this step of the project: {original_goal}

CONTEXT: Silently consider to yourself the following broader context before you decide on and provide the deliverable for this step of the project: {json.dumps(plan)}

YOUR INSTRUCTION: Given all this information, now write specific suggestions for improvement of the draft content for this step of the project. Focus on key areas that need revision to better align with the user's original goal, adhere to the evaluation criteria for this step, and that make sense in the context of the entire project, based on the broader context you now have. Do not provide a refined draft, do not provide recommended revisions for other steps, only provide your recommended content revisions requests for the draft content in the following step: {step}:
{draft}"""

        if verbose:
            print(f"\nPrompt for generating revision request for step '{step}':\n{prompt}\n")
            flush()

        # OTEL: Add a span for each LLM call
        with tracer.start_as_current_span(
            f"llm.openai.generate_revision_request.{step.replace(' ', '_')}",
            kind=SpanKind.CLIENT,
            attributes={
                ### MODIFICATION ###
                # Added standardized and custom attributes for rich, evaluatable traces.
                "openinference.span.kind": OIKind.LLM.value,
                "gen_ai.system": "openai",
                "gen_ai.request.model": "gpt-4",
                "gen_ai.operation.name": "chat",
                "agento.step_type": "critique",
                "agento.step_name": step,
                "agento.user_goal": original_goal,
                "agento.draft_content": draft,
                "agento.plan_item": current_plan_item_content,
                ### END MODIFICATION ###
            },
        ) as span:
            try:
                response = openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": prompt}]
                )
                set_openai_tokens(span, response)
                revision_content = response.choices[0].message.content
                set_ai_response_attribute(span, revision_content)
                span.set_status(Status(StatusCode.OK))

                revision_requests[step] = revision_content

                if verbose:
                    print(f"Revision request for step '{step}':\n{revision_content}\n")
                    flush()

                # Introduce a delay to respect rate limits
                time.sleep(delay_in_seconds)

            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                print(f"Error calling OpenAI API for step '{step}': {e}")
                flush()

    return revision_requests

# ---------------------------------------------------------------------------
#  Function to compile final plan
# ---------------------------------------------------------------------------
def compile_final_plan(drafts: Dict[str, str], plan: Optional[Dict], goal: str) -> ProjectPlan:
    """
    This function creates a ProjectPlan object from the drafts
    It ensures that the final plan conforms to the standardized schema
    """
    if plan is None:
        plan = {}

    Detailed_Outline = []
    for step_item in plan.get("Detailed_Outline", []):
        step_name = step_item["name"]
        Detailed_Outline.append({
            "name": step_name,
            "content": drafts.get(step_name, "")
        })

    Evaluation_Criteria = plan.get("Evaluation_Criteria", {})

    final_plan = ProjectPlan(
        Original_Goal=goal,
        Title=plan.get("Title", "Default Title"),
        Overall_Summary=plan.get("Overall_Summary", "No summary available"),
        Detailed_Outline=Detailed_Outline,
        Evaluation_Criteria=Evaluation_Criteria,
        revision_requests=plan.get("revision_requests", {}),
        Success_Measures=plan.get("Success_Measures", ["No success measures provided"])
    )
    return final_plan

# ---------------------------------------------------------------------------
#  Function to convert plan to Markdown
# ---------------------------------------------------------------------------
def convert_to_markdown(plan):
    """
    Converts the project plan to Markdown format.
    """
    md_content = f"# {plan.Title}\n\n"
    md_content += f"## Overall Summary\n\n{plan.Overall_Summary}\n\n"
    md_content += "## Detailed Outline\n\n"
    for step in plan.Detailed_Outline:
        md_content += f"### {step['name']}\n\n"
        md_content += f"{step['content']}\n\n"
    md_content += "## Evaluation Criteria\n\n"
    for step, criteria in plan.Evaluation_Criteria.items():
        md_content += f"### {step}\n\n{criteria}\n\n"
    md_content += "## Revision Requests\n\n"
    if plan.revision_requests:
        for step, request in plan.revision_requests.items():
            md_content += f"### {step}\n\n{request}\n\n"
    md_content += "## Success Measures\n\n"
    for measure in plan.Success_Measures:
        md_content += f"- {measure}\n"
    return md_content

# ---------------------------------------------------------------------------
#  Function to save file
# ---------------------------------------------------------------------------
def save_file(content, filename):
    """
    Saves the given content to a file.
    """
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        logging.info(f"Saved {filename}")
    except IOError as e:
        logging.error(f"Error saving file {filename}: {e}")

# ---------------------------------------------------------------------------
#  Function to save plan outputs (JSON and Markdown)
# ---------------------------------------------------------------------------
def save_plan_outputs(plan: ProjectPlan):
    """
    This function saves the project plan as both JSON and Markdown
    It includes validation to ensure the plan conforms to the ProjectPlan schema
    """
    current_date = datetime.now().strftime("%Y-%m-%d")
    current_datetime = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    notebook_name = os.path.splitext(os.path.basename(__file__))[0]

    # Save JSON files
    json_filename = "project_plan.json"
    json_filename_with_date = f"project_plan_{current_datetime}.json"

    # Validate and serialize the ProjectPlan using Pydantic
    try:
        plan_dict = plan.model_dump()  # Convert to dictionary using Pydantic
        json_content = json.dumps(plan_dict, indent=2)  # Serialize the dictionary
        save_file(json_content, json_filename)
        save_file(json_content, json_filename_with_date)
    except ValidationError as e:
        logging.error(f"The generated plan does not conform to the schema: {e}")

    # Save Markdown file
    md_filename = f"{notebook_name}-{current_date}-InitialPlan.md"
    md_content = convert_to_markdown(plan)
    save_file(md_content, md_filename)

# ---------------------------------------------------------------------------
#  Context Propagation Helpers
# ---------------------------------------------------------------------------
def read_trace_context():
    """
    Reads the trace context from a file if it exists, for OTEL context propagation.
    """
    try:
        if Path("trace.context").exists():
            with open("trace.context", "r", encoding="utf-8") as f:
                carrier = json.load(f)
            return propagate.extract(carrier)
    except Exception as e:
        logging.warning(f"Could not read trace.context: {e}")
    return None

def write_trace_context():
    """
    Writes the current trace context to a file for downstream modules.
    """
    try:
        carrier = {}
        propagate.inject(carrier)
        Path("trace.context").write_text(json.dumps(carrier))
    except Exception as e:
        logging.warning(f"Could not write trace.context: {e}")

# ---------------------------------------------------------------------------
#  Main execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    with tee_output():
        # --- Context Propagation: Read parent context if available ---
        parent_context = read_trace_context()
        with tracer.start_as_current_span(
            "agento.pipeline.develop_plan",
            context=parent_context,
            kind=SpanKind.INTERNAL,
            attributes={"openinference.span.kind": OIKind.AGENT.value},
        ) as root_span:
            print("Script starting...")

            plan = load_plan_structure()
            if plan is None:
                print("Error: Could not load or validate plan structure. Exiting.")
                root_span.set_status(Status(StatusCode.ERROR, "Could not load or validate plan structure"))
                sys.exit(1)

            original_goal = plan.get("Original_Goal", "")
            if not original_goal:
                print("Error: 'Original_Goal' not found in loaded plan. Exiting.")
                root_span.set_status(Status(StatusCode.ERROR, "'Original_Goal' not found in loaded plan"))
                sys.exit(1)
            
            # Set user_goal on the root span
            root_span.set_attribute("user_goal", original_goal)

            drafts = develop_drafts(plan, original_goal)

            revision_requests = generate_revision_requests(drafts, plan, original_goal)

            plan["revision_requests"] = revision_requests

            # Print revision requests for verification
            print("\nGenerated Revision Requests:")
            for step, request in revision_requests.items():
                print(f"\n{step}:\n{request}")
            flush()

            final_plan = compile_final_plan(drafts, plan, original_goal)

            print("Final Comprehensive Project Plan:")
            print(json.dumps(final_plan.model_dump(), indent=2))
            flush()

            save_plan_outputs(final_plan)

            print(f"\nProject plan saved.")
            print("You can now run the next module to continue the iterative refinement process.")
            flush()

            write_trace_context()

        trace.get_tracer_provider().force_flush()