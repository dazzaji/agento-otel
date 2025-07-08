# RUN:  python3 2_Revise-Plan-Stable.py


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
# Add these imports at the top with other imports
from dataclasses import dataclass
from typing import Dict, List, Optional, Union, Tuple
import sys
import subprocess
import contextlib

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
            stderr=subprocess.STDOUT,  # Redirect stderr to stdout
            text=True,  # Use text mode
            bufsize=1  # Line buffered
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

def flush():
    sys.stdout.flush()

@dataclass
class RevisionContext:
    """Maintains the context of a revision session."""
    step_name: str
    original_content: str
    revision_request: str
    claude_messages: List[Dict[str, str]]
    gemini_history: List[Dict[str, str]]
    current_iteration: int

def _format_revision_history(history: List[Dict[str, str]]) -> str:
    """Formats the revision history for inclusion in prompts."""
    if not history:
        return "No previous revisions"
        
    formatted = []
    for i, msg in enumerate(history):
        if msg["role"] == "model":  # For Gemini responses
            formatted.append(f"Revision {i//2 + 1}:\n```\n{msg['content']}\n```")
        elif msg["role"] == "assistant":  # For Claude responses
            formatted.append(f"Instruction {i//2 + 1}:\n```\n{msg['content']}\n```")
    
    return "\n\n".join(formatted)

# Set up logging
logging.basicConfig(level=logging.DEBUG, filename='script.log', filemode='w')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Anthropic and Gemini API clients
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


# Configuration for LLM interactions
MAX_ITERATIONS = 3  # Maximum number of revision loops
CLAUDE_MODEL = "claude-3-5-sonnet-20241022"  # Specific Claude model
GEMINI_MODEL = "gemini-1.5-pro"  # Specific Gemini model


def load_revised_plan(filename: str) -> Optional[Dict]:
    """Loads the revised project plan from JSON."""
    try:
        print(f"Attempting to open {filename}...")  # Add this line
        with open(filename, "r") as f:
            data = json.load(f)
            print(f"Successfully loaded {filename}")  # Add this line
            return data
    except FileNotFoundError:
        print(f"Error: Could not find {filename}")  # Add this line
        logger.error(f"Error loading revised project plan: File not found")
        return None
    except (json.JSONDecodeError) as e:
        print(f"Error: {filename} is not valid JSON")  # Add this line
        logger.error(f"Error loading revised project plan: {e}")
        return None


def get_claude_response(
    client: anthropic.Anthropic,
    context: RevisionContext,
    new_prompt: str
) -> Optional[str]:
    """Gets a response from Claude using conversation history."""
    try:
        print("Sending request to Claude...")
        # Add new prompt to message history
        context.claude_messages.append({"role": "user", "content": new_prompt})
        
        response = client.messages.create(
            model=CLAUDE_MODEL,
            messages=context.claude_messages,
            max_tokens=2048,
            temperature=0,
            system="You are a project manager helping revise content for this project."  # Add system message here
        )
        
        # Store Claude's response in history
        response_text = response.content[0].text
        context.claude_messages.append({"role": "assistant", "content": response_text})
        print("Received response from Claude")
        
        return response_text
    except Exception as e:
        print(f"Error in get_claude_response: {str(e)}")
        logger.error(f"Error getting response from Claude: {e}")
        return None

def get_gemini_response(
    model: genai.GenerativeModel,
    context: RevisionContext,
    new_prompt: str
) -> Optional[str]:
    """Gets a response from Gemini with conversation history."""
    try:
        print("Starting Gemini response generation...")
        print(f"History length: {len(context.gemini_history)}")
        
        # Convert history to Gemini's expected format
        formatted_history = []
        for msg in context.gemini_history:
            formatted_history.append({
                "role": "user" if msg["role"] == "user" else "model",
                "parts": [{"text": msg["content"]}]
            })
        
        # Create chat with formatted history
        chat = model.start_chat(history=formatted_history)
        print("Chat session created")
        
        # Send message and get response
        response = chat.send_message(new_prompt)
        print("Message sent to Gemini")
        response_text = response.text
        print("Response received from Gemini")
        
        # Store the interaction in our context format
        context.gemini_history.append({"role": "user", "content": new_prompt})
        context.gemini_history.append({"role": "model", "content": response_text})
        print("History updated")
        
        return response_text
    except Exception as e:
        print(f"Error in get_gemini_response: {str(e)}")
        logger.error(f"Error getting response from Gemini: {e}")
        return None


def revise_step_with_llms(
    plan: Dict,
    step: Dict,
    revision_request: str,
    anthropic_client: anthropic.Anthropic,
    gemini_model: genai.GenerativeModel,
    verbose: bool = True
) -> Tuple[Optional[Dict], RevisionContext]:
    """Orchestrates the revision process with context retention."""
    
    # Initialize revision context
    context = RevisionContext(
        step_name=step["name"],
        original_content=step["content"],
        revision_request=revision_request,
        claude_messages=[],  # Will be populated during revision
        gemini_history=[],   # Will be populated during revision
        current_iteration=1
    )
    
    while context.current_iteration <= MAX_ITERATIONS:
        logger.info(f"---- Iteration {context.current_iteration}: Revising '{step['name']}' ----")

        # 1. Claude generates instructions for Gemini
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
        
        gemini_prompt = get_claude_response(anthropic_client, context, claude_prompt)
        if not gemini_prompt:
            return None, context
            
        if verbose:
            print(f"\nClaude's Instructions:\n{gemini_prompt}\n")

        # 2. Gemini performs the revision
        print("\nSending to Gemini for revision...")  # Added debug print
        revised_content = get_gemini_response(gemini_model, context, gemini_prompt)
        if not revised_content:
            print("Failed to get response from Gemini")  # Added debug print
            return None, context
            
        if verbose:
            print(f"\nGemini's Revision:\n{revised_content}\n")

        # 3. Claude reviews the revision
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
        
        verdict = get_claude_response(anthropic_client, context, review_prompt)
        if not verdict:
            return None, context
            
        if verbose:
            print(f"\nClaude's Verdict:\n{verdict}\n")

        if "YES" in verdict.upper():
            step["content"] = revised_content
            logger.info(f"Revision accepted for step '{step['name']}'")
            return plan, context
            
        context.current_iteration += 1
        
    logger.warning(f"Maximum iterations reached for step '{step['name']}'")
    return plan, context


def further_revise_plan(
    plan: Dict,
    anthropic_client: anthropic.Anthropic,
    gemini_model: genai.GenerativeModel,
    verbose: bool = True
) -> Tuple[Optional[Dict], Dict[str, RevisionContext]]:
    """Manages the revision process with context tracking."""
    try:
        print("Initializing revision process...")  # Add this
        revision_contexts = {}  # Store contexts for all revisions
        total_steps = len([step for step in plan["Detailed_Outline"]
                        if step["name"] in plan["revision_requests"]])
        print(f"Found {total_steps} steps to revise")  # Add this
        completed_steps = 0

        for step in plan["Detailed_Outline"]:
            step_name = step["name"]
            if step_name in plan["revision_requests"]:
                print(f"\nStarting revision for step: {step_name}")  # Add this
                revision_request = plan["revision_requests"][step_name]
                print(f"Revision request: {revision_request[:100]}...")  # Add this
                
                try:
                    plan, context = revise_step_with_llms(
                        plan, step, revision_request,
                        anthropic_client, gemini_model, verbose
                    )
                except Exception as e:
                    print(f"Error in revise_step_with_llms: {str(e)}")  # Add this
                    logger.error(f"Error in revise_step_with_llms: {e}")
                    return None, revision_contexts
                
                if plan is None:
                    print(f"Failed to revise step: {step_name}")  # Add this
                    return None, revision_contexts
                    
                revision_contexts[step_name] = context
                completed_steps += 1
                print(f"Progress: {completed_steps}/{total_steps} steps revised")

        return plan, revision_contexts
    except Exception as e:
        print(f"Error in further_revise_plan: {str(e)}")  # Add this
        logger.error(f"Error in further_revise_plan: {e}")
        return None, {}


def convert_to_markdown(plan: Dict) -> str:
    """Converts the project plan to Markdown format."""
    md_content = f"# {plan['Title']}\n\n"
    md_content += f"## Overall Summary\n\n{plan['Overall_Summary']}\n\n"
    md_content += "## Detailed Outline\n\n"
    for step in plan["Detailed_Outline"]:
        md_content += f"### {step['name']}\n\n{step['content']}\n\n"
    return md_content


def save_revised_plan(plan: Dict, file_prefix: str) -> None: # Takes file prefix as argument
    """Saves the revised plan in JSON and Markdown formats with desired naming."""

    json_filename = "RevisedPlan.json"  # Fixed filename
    json_filename_with_date = f"{file_prefix}.json"  #  Filename with date and time from the prefix

    with open(json_filename, "w") as f:
        json.dump(plan, f, indent=4)
    print(f"Revised plan saved as '{json_filename}'")

    with open(json_filename_with_date, "w") as f: # Save with prefix (containing date-time)
        json.dump(plan, f, indent=4)
    print(f"Revised plan saved as '{json_filename_with_date}'")

    md_filename = f"{file_prefix}-RevisedPlan.md"  # Filename with prefix and suffix
    md_content = convert_to_markdown(plan)
    with open(md_filename, "w") as f:
        f.write(md_content)
    print(f"Revised plan saved as '{md_filename}'")


if __name__ == "__main__":
    with tee_output():  # Wrap everything in tee_output
        print("Script starting...")
        current_datetime = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        notebook_name = os.path.splitext(os.path.basename(__file__))[0]
        print(f"Looking for project_plan.json...")
        revised_plan = load_revised_plan("project_plan.json")
        
        if revised_plan:
            print("Found and loaded project_plan.json")
            print("Checking for revision requests...")
            
            # Check if there are any revision requests
            revision_count = len([step for step in revised_plan["Detailed_Outline"] 
                                if step["name"] in revised_plan["revision_requests"]])
            print(f"Found {revision_count} steps that need revision")
            
            print("Starting revision process...")
            try:
                further_revised_plan, revision_contexts = further_revise_plan(
                    revised_plan,
                    anthropic_client,
                    genai.GenerativeModel(GEMINI_MODEL),
                    verbose=True
                )
                
                if further_revised_plan:
                    file_prefix = f"{notebook_name}-{current_datetime}"
                    save_revised_plan(further_revised_plan, file_prefix)
                else:
                    print("Error: Revision process failed")
            except Exception as e:
                print(f"Error during revision process: {str(e)}")
                logger.error(f"Error during revision process: {e}")
        else:
            print("Error: Could not load project plan")

    # Examples of the filenames that would be generated (no leading zeros in the timestamps now)
    # * `RevisedPlan.json` 
    # * `agento_02_revise_reprompt-2024-11-04_19-30-5.json` (example)
    # * `agento_02_revise_reprompt-2024-11-04_19-30-5-RevisedPlan.md` (example)