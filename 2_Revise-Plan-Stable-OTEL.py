# RUN:  python3 2_Revise-Plan-Stable-OTEL.py

import json
import os
from dotenv import load_dotenv
import anthropic
import google.generativeai as genai
from google.api_core import retry
from google.api_core import exceptions as google_exceptions
import logging
from tqdm import tqdm
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Union, Tuple
import sys
import subprocess
import contextlib

### MODIFICATION START ###
# Added OpenTelemetry imports to support tracing
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
### MODIFICATION END ###

@contextlib.contextmanager
def tee_output(filename=None):
    if filename is None:
        script_name = os.path.splitext(os.path.basename(__file__))[0]
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{script_name}_{timestamp}_output.log"
    try:
        process = subprocess.Popen(
            ['tee', filename],
            stdin=subprocess.PIPE,
            stdout=sys.stdout,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = process.stdin
        yield
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        if process.stdin:
            process.stdin.close()
        process.wait()

def flush():
    sys.stdout.flush()

@dataclass
class RevisionContext:
    step_name: str
    original_content: str
    revision_request: str
    claude_messages: List[Dict[str, str]]
    gemini_history: List[Dict[str, str]]
    current_iteration: int

def _format_revision_history(history: List[Dict[str, str]]) -> str:
    if not history:
        return "No previous revisions"
    formatted = []
    for i, msg in enumerate(history):
        if msg["role"] == "model":
            formatted.append(f"Revision {i//2 + 1}:\n```\n{msg['content']}\n```")
        elif msg["role"] == "assistant":
            formatted.append(f"Instruction {i//2 + 1}:\n```\n{msg['content']}\n```")
    return "\n\n".join(formatted)

logging.basicConfig(level=logging.INFO, filename='script.log', filemode='w') # Changed level to INFO for production
logger = logging.getLogger(__name__)

load_dotenv()
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

### MODIFICATION START ###
# Added OTEL tracer setup
MODULE_NAME = Path(__file__).stem
resource = Resource.create({
    "service.name": "agento",
    "service.version": "1.0.0",
    "agento.module": MODULE_NAME,
    "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    "service.instance.id": str(uuid.uuid4()),
})
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
    endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
    insecure=True,
)))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

def exit_gracefully(signum=None, frame=None):
    span = trace.get_current_span()
    if span and span.is_recording() and span.status.status_code is StatusCode.UNSET:
        span.set_status(Status(StatusCode.OK))
        span.end()
    if trace.get_tracer_provider():
        trace.get_tracer_provider().force_flush()
    sys.exit(0)

signal.signal(signal.SIGTERM, exit_gracefully)
### MODIFICATION END ###

MAX_ITERATIONS = 3
CLAUDE_MODEL = "claude-3-5-sonnet-20240620" # Updated model
GEMINI_MODEL = "gemini-1.5-pro"

def load_revised_plan(filename: str) -> Optional[Dict]:
    try:
        print(f"Attempting to open {filename}...")
        with open(filename, "r") as f:
            data = json.load(f)
            print(f"Successfully loaded {filename}")
            return data
    except FileNotFoundError:
        print(f"Error: Could not find {filename}")
        logger.error(f"Error loading revised project plan: File not found")
        return None
    except json.JSONDecodeError as e:
        print(f"Error: {filename} is not valid JSON")
        logger.error(f"Error loading revised project plan: {e}")
        return None

def get_claude_response(client: anthropic.Anthropic, context: RevisionContext, new_prompt: str, task_description: str) -> Optional[str]:
    ### MODIFICATION START ###
    # Wrapped the LLM call in a new OTEL span
    with tracer.start_as_current_span(
        f"llm.anthropic.{task_description}",
        kind=SpanKind.CLIENT,
        attributes={
            "openinference.span.kind": OIKind.LLM.value,
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": CLAUDE_MODEL,
            "agento.step_type": task_description, # e.g., 'revision_instruction' or 'revision_verdict'
            "agento.step_name": context.step_name,
        }
    ) as span:
    ### MODIFICATION END ###
        try:
            print(f"Sending request to Claude for: {task_description}...")
            context.claude_messages.append({"role": "user", "content": new_prompt})
            response = client.messages.create(
                model=CLAUDE_MODEL,
                messages=context.claude_messages,
                max_tokens=4096, # Increased max tokens
                temperature=0,
                system="You are a project manager helping revise content for this project."
            )
            response_text = response.content[0].text
            context.claude_messages.append({"role": "assistant", "content": response_text})
            print("Received response from Claude")
            ### MODIFICATION START ###
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)
            span.set_attribute("gen_ai.response.content", response_text)
            span.set_status(Status(StatusCode.OK))
            ### MODIFICATION END ###
            return response_text
        except Exception as e:
            print(f"Error in get_claude_response: {str(e)}")
            logger.error(f"Error getting response from Claude: {e}")
            ### MODIFICATION START ###
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            ### MODIFICATION END ###
            return None

def get_gemini_response(model: genai.GenerativeModel, context: RevisionContext, new_prompt: str) -> Optional[str]:
    ### MODIFICATION START ###
    with tracer.start_as_current_span(
        "llm.gemini.revision_draft",
        kind=SpanKind.CLIENT,
        attributes={
            "openinference.span.kind": OIKind.LLM.value,
            "gen_ai.system": "gemini",
            "gen_ai.request.model": GEMINI_MODEL,
            "agento.step_type": "revision_draft",
            "agento.step_name": context.step_name,
        }
    ) as span:
    ### MODIFICATION END ###
        try:
            print("Starting Gemini response generation...")
            formatted_history = [{"role": "user" if msg["role"] == "user" else "model", "parts": [{"text": msg["content"]}]} for msg in context.gemini_history]
            chat = model.start_chat(history=formatted_history)
            
            # Use async call with retry for robustness
            @retry.Retry(predicate=retry.if_exception_type(google_exceptions.ResourceExhausted))
            def send_message_with_retry():
                return chat.send_message(new_prompt)
            
            response = send_message_with_retry()
            
            response_text = response.text
            print("Response received from Gemini")
            context.gemini_history.append({"role": "user", "content": new_prompt})
            context.gemini_history.append({"role": "model", "content": response_text})

            ### MODIFICATION START ###
            if response.usage_metadata:
                span.set_attribute("gen_ai.usage.input_tokens", response.usage_metadata.prompt_token_count)
                span.set_attribute("gen_ai.usage.output_tokens", response.usage_metadata.candidates_token_count)
            span.set_attribute("gen_ai.response.content", response_text)
            span.set_status(Status(StatusCode.OK))
            ### MODIFICATION END ###

            return response_text
        except Exception as e:
            print(f"Error in get_gemini_response: {str(e)}")
            logger.error(f"Error getting response from Gemini: {e}")
            ### MODIFICATION START ###
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            ### MODIFICATION END ###
            return None

def revise_step_with_llms(plan: Dict, step: Dict, revision_request: str, anthropic_client: anthropic.Anthropic, gemini_model: genai.GenerativeModel, verbose: bool = True) -> Tuple[Optional[Dict], RevisionContext]:
    context = RevisionContext(
        step_name=step["name"],
        original_content=step["content"],
        revision_request=revision_request,
        claude_messages=[],
        gemini_history=[],
        current_iteration=1
    )
    
    ### MODIFICATION START ###
    # Added a parent span to trace the entire revision chain for a single step
    with tracer.start_as_current_span(f"agento.chain.revise_step.{context.step_name.replace(' ', '_')}", kind=SpanKind.INTERNAL, attributes={"openinference.span.kind": OIKind.CHAIN.value, "agento.step_name": context.step_name}) as chain_span:
    ### MODIFICATION END ###
        while context.current_iteration <= MAX_ITERATIONS:
            logger.info(f"---- Iteration {context.current_iteration}: Revising '{step['name']}' ----")

            claude_prompt = f"""
            Review the following step '{context.step_name}' considering previous revisions:
            Original content:
            ```
            {context.original_content}
            ```
            
            Revision request:
            ```
            {context.revision_request}
            ```
            
            Previous revisions (if any):
            {_format_revision_history(context.gemini_history)}
            
            Provide detailed instructions to Gemini for the next revision.
            """
            
            gemini_prompt = get_claude_response(anthropic_client, context, claude_prompt, "revision_instruction")
            if not gemini_prompt: return None, context
            if verbose: print(f"\nClaude's Instructions:\n{gemini_prompt}\n")

            revised_content = get_gemini_response(gemini_model, context, gemini_prompt)
            if not revised_content:
                print("Failed to get response from Gemini")
                return None, context
            if verbose: print(f"\nGemini's Revision:\n{revised_content}\n")

            review_prompt = f"""
            Review Gemini's latest revision:
            ```
            {revised_content}
            ```
            
            Original revision request:
            ```
            {context.revision_request}
            ```
            
            Previous revision history:
            {_format_revision_history(context.gemini_history)}
            
            Does this revision meet all requirements? Respond with:
            * YES - if complete and ready for hand-off
            * NO - if changes needed, with specific feedback
            """
            
            verdict = get_claude_response(anthropic_client, context, review_prompt, "revision_verdict")
            if not verdict: return None, context
            if verbose: print(f"\nClaude's Verdict:\n{verdict}\n")

            if "YES" in verdict.upper():
                step["content"] = revised_content
                logger.info(f"Revision accepted for step '{step['name']}'")
                ### MODIFICATION START ###
                # Create a span to explicitly log the accepted revision for evaluation
                with tracer.start_as_current_span(f"agento.event.accepted_revision", kind=SpanKind.INTERNAL, attributes={"openinference.span.kind": OIKind.AGENT.value}) as event_span:
                    event_span.set_attribute("agento.step_type", "accepted_revision")
                    event_span.set_attribute("agento.step_name", context.step_name)
                    event_span.set_attribute("agento.revision_request", context.revision_request)
                    event_span.set_attribute("agento.final_content", revised_content)
                    event_span.set_attribute("agento.iterations_taken", context.current_iteration)
                chain_span.set_status(Status(StatusCode.OK, "Revision Accepted"))
                ### MODIFICATION END ###
                return plan, context
            
            context.current_iteration += 1

        logger.warning(f"Maximum iterations reached for step '{step['name']}'")
        ### MODIFICATION START ###
        # Create a span to log the timed-out state for evaluation
        with tracer.start_as_current_span(f"agento.event.timed_out_revision", kind=SpanKind.INTERNAL, attributes={"openinference.span.kind": OIKind.AGENT.value}) as event_span:
            last_draft = context.gemini_history[-1]['content'] if context.gemini_history else "No draft was generated."
            final_critique = context.claude_messages[-1]['content'] if context.claude_messages else "No final critique was generated."
            event_span.set_attribute("agento.step_type", "timed_out_revision")
            event_span.set_attribute("agento.step_name", context.step_name)
            event_span.set_attribute("agento.revision_request", context.revision_request)
            event_span.set_attribute("agento.last_attempted_draft", last_draft)
            event_span.set_attribute("agento.final_critique", final_critique)
        chain_span.set_status(Status(StatusCode.ERROR, "Max iterations reached"))
        ### MODIFICATION END ###
        return plan, context

def further_revise_plan(plan: Dict, anthropic_client: anthropic.Anthropic, gemini_model: genai.GenerativeModel, verbose: bool = True) -> Tuple[Optional[Dict], Dict[str, RevisionContext]]:
    try:
        print("Initializing revision process...")
        revision_contexts = {}
        steps_to_revise = [step for step in plan["Detailed_Outline"] if step["name"] in plan.get("revision_requests", {})]
        total_steps = len(steps_to_revise)
        print(f"Found {total_steps} steps to revise")

        for i, step in enumerate(steps_to_revise):
            step_name = step["name"]
            print(f"\nStarting revision for step {i+1}/{total_steps}: {step_name}")
            revision_request = plan["revision_requests"][step_name]
            
            try:
                plan, context = revise_step_with_llms(
                    plan, step, revision_request,
                    anthropic_client, gemini_model, verbose
                )
            except Exception as e:
                print(f"Error in revise_step_with_llms for step '{step_name}': {str(e)}")
                logger.error(f"Error in revise_step_with_llms: {e}")
                return None, revision_contexts
            
            if plan is None:
                print(f"Failed to revise step: {step_name}")
                return None, revision_contexts
                
            revision_contexts[step_name] = context
            print(f"Progress: {i+1}/{total_steps} steps revised")

        return plan, revision_contexts
    except Exception as e:
        print(f"Error in further_revise_plan: {str(e)}")
        logger.error(f"Error in further_revise_plan: {e}")
        return None, {}

def convert_to_markdown(plan: Dict) -> str:
    md_content = f"# {plan['Title']}\n\n"
    md_content += f"## Overall Summary\n\n{plan['Overall_Summary']}\n\n"
    md_content += "## Detailed Outline\n\n"
    for step in plan["Detailed_Outline"]:
        md_content += f"### {step['name']}\n\n{step['content']}\n\n"
    md_content += "## Revision Requests\n\n"
    if plan.get("revision_requests"):
        for step_name, request in plan["revision_requests"].items():
            md_content += f"### {step_name}\n\n{request}\n\n"
    return md_content

def save_revised_plan(plan: Dict, file_prefix: str) -> None:
    json_filename = "RevisedPlan.json"
    json_filename_with_date = f"{file_prefix}.json"
    with open(json_filename, "w") as f:
        json.dump(plan, f, indent=4)
    print(f"Revised plan saved as '{json_filename}'")
    with open(json_filename_with_date, "w") as f:
        json.dump(plan, f, indent=4)
    print(f"Revised plan saved as '{json_filename_with_date}'")
    md_filename = f"{file_prefix}-RevisedPlan.md"
    md_content = convert_to_markdown(plan)
    with open(md_filename, "w") as f:
        f.write(md_content)
    print(f"Revised plan saved as '{md_filename}'")

### MODIFICATION START ###
# Added context propagation helpers, necessary for multi-module traces
def read_trace_context():
    try:
        if Path("trace.context").exists():
            with open("trace.context", "r", encoding="utf-8") as f:
                return propagate.extract(json.load(f))
    except Exception as e:
        logging.warning(f"Could not read trace.context: {e}")
    return None

def write_trace_context():
    try:
        carrier = {}
        propagate.inject(carrier)
        Path("trace.context").write_text(json.dumps(carrier))
    except Exception as e:
        logging.warning(f"Could not write trace.context: {e}")
### MODIFICATION END ###

if __name__ == "__main__":
    with tee_output():
        ### MODIFICATION START ###
        parent_context = read_trace_context()
        with tracer.start_as_current_span(
            "agento.pipeline.revise_plan",
            context=parent_context,
            kind=SpanKind.INTERNAL,
            attributes={"openinference.span.kind": OIKind.AGENT.value}
        ) as root_span:
        ### MODIFICATION END ###
            print("Script starting...")
            current_datetime = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            notebook_name = os.path.splitext(os.path.basename(__file__))[0]
            print(f"Looking for project_plan.json...")
            revised_plan = load_revised_plan("project_plan.json")
            
            if revised_plan:
                ### MODIFICATION START ###
                # Set user_goal on the root span for context
                user_goal = revised_plan.get("Original_Goal", "Goal not found in plan.")
                root_span.set_attribute("user_goal", user_goal)
                ### MODIFICATION END ###

                print("Found and loaded project_plan.json")
                revision_requests = revised_plan.get("revision_requests", {})
                if revision_requests:
                    print(f"Found {len(revision_requests)} steps that need revision")
                    print("Starting revision process...")
                    try:
                        further_revised_plan, revision_contexts = further_revise_plan(
                            revised_plan,
                            anthropic_client,
                            genai.GenerativeModel(GEMINI_MODEL),
                            verbose=True
                        )
                        
                        if further_revised_plan:
                            ### MODIFICATION START ###
                            # Create a final span for holistic evaluation of the entire plan
                            with tracer.start_as_current_span("agento.event.holistic_review", kind=SpanKind.INTERNAL, attributes={"openinference.span.kind": OIKind.EVALUATOR.value}) as final_span:
                                final_plan_json = json.dumps(further_revised_plan, indent=2)
                                final_span.set_attribute("agento.step_type", "holistic_review")
                                final_span.set_attribute("agento.user_goal", user_goal)
                                final_span.set_attribute("agento.final_plan_content", final_plan_json)
                            ### MODIFICATION END ###
                            file_prefix = f"{notebook_name}-{current_datetime}"
                            save_revised_plan(further_revised_plan, file_prefix)
                        else:
                            print("Error: Revision process failed")
                            root_span.set_status(Status(StatusCode.ERROR, "Revision process failed"))
                    except Exception as e:
                        print(f"Error during revision process: {str(e)}")
                        logger.error(f"Error during revision process: {e}")
                        root_span.set_status(Status(StatusCode.ERROR, str(e)))
                else:
                    print("No revision requests found in the plan. Nothing to do.")
            else:
                print("Error: Could not load project plan")
                root_span.set_status(Status(StatusCode.ERROR, "Could not load project_plan.json"))
            
            ### MODIFICATION START ###
            # Propagate context to potential next modules
            write_trace_context()
            ### MODIFICATION END ###

    ### MODIFICATION START ###
    # Ensure all spans are flushed before exiting
    trace.get_tracer_provider().force_flush()
    ### MODIFICATION END ###