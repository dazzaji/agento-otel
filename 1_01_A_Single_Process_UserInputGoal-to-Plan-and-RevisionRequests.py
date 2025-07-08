# Note: This scrips DOES EVERYTHING from user goal input to revision requets in ONE PROCESS!
# Before running this script, ensure I have installed the dependencies and required libraries:
# python -m pip install -U openai google-generativeai python-dotenv tqdm pydantic
# python -m pip install -r requirements.txt

# RUN: python3 1_01_A_Single_Process_UserInputGoal-to-Plan-and-RevisionRequests.py

# Possible goal: I need to create a new python script to set up creative collaborative LLM-based agents design that uses OpenAI, Anthropic, and Google APIs for their most powerful LLMs in a way that the agents chat with each other to work on achieving a goal that the user inputs. The system brainstorms potential approachs and choose the best (based on criteria the LLM's generate in the context of the goal) and then develops a plan, then implements and refines the plan until it is finalized. Then the plan guides generation, refinements, and finalization of the final output. Each stage of development is created, refined, and agreed as finished by the 3 agents and is then exported as JSON and Markdown for the user to have.

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

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
            ['tee', filename],  # No need for -a, tee appends by default
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
        
        yield  # Your script's main execution happens here
    finally:
        # Restore original stdout/stderr
        sys.stdout, sys.stderr = old_stdout, old_stderr
        
        # Close tee's stdin and wait for it to finish
        if process.stdin:
            process.stdin.close()
        process.wait()



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

# Get project goal from user
project_goal = input("Please enter your project goal: ")

# Verbose mode
verbose = True

# Define Pydantic model for the standardized schema 
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
    revision_requests: Dict[str, str] = Field(..., description="Revision suggestions for each step")
    Success_Measures: List[str]

# Function to generate initial plan (using Gemini 1.5 Pro)
def generate_plan(goal):
    prompt = f"""
    You are a top consultant called in to deliver a final version of what the user needs correctly, completely, and at high quality.
    Create a comprehensive set of project deliverables, identifying each deliverable step by step, in JSON format to achieve the following goal: {goal}

    The JSON should strictly adhere to this template:
    {{
      "Title": "...",
      "Overall_Summary": "...",
      "Detailed_Outline": [
        {{"name": "Step 1", "content": "..."}},
        {{"name": "Step 2", "content": "..."}},
        ...
      ],
      "Evaluation_Criteria": {{
        "Step 1": "Criteria for Step 1",
        "Step 2": "Criteria for Step 2",
        ...
      }},
      "Success_Measures": ["...", "..."]
    }}

    Ensure that each step in the "Detailed_Outline" has a corresponding entry in the "Evaluation_Criteria".
    Your response must be valid JSON and nothing else. Do not include any explanations or text outside of the JSON structure.
    """

    if verbose:
        print(f"\nPrompt for generating plan:\n{prompt}\n")
        flush()

    class Step(typing.TypedDict):
        name: str
        content: str

    class EvaluationCriteriaItem(typing.TypedDict):
        name: str
        criteria: str

    class ProjectPlanSchema(typing.TypedDict):
        Title: str
        Overall_Summary: str
        Detailed_Outline: list[Step]
        Evaluation_Criteria: dict[str, str]  # Corrected type hint
        Success_Measures: list[str]

    model = genai.GenerativeModel('gemini-1.5-pro',
                                  generation_config={
                                      "temperature": 0.1,
                                      "top_p": 1,
                                      "top_k": 1,
                                      "max_output_tokens": 4096
                                  })

    @retry.Retry(predicate=retry.if_exception_type(
        google_exceptions.ResourceExhausted,
        google_exceptions.ServiceUnavailable,
        google_exceptions.DeadlineExceeded,
        google_exceptions.InternalServerError
    ), deadline=90) # Increased timeout to 1.5 minutes (90 seconds)
    def generate_with_retry():
        return model.generate_content(prompt)

    try:
        with tqdm(total=1, desc="Generating plan...") as pbar:
            response = generate_with_retry()
            pbar.update(1)

        try:
            # Extract JSON from the response
            json_start = response.text.find('{')
            json_end = response.text.rfind('}') + 1
            json_str = response.text[json_start:json_end]
            plan = json.loads(json_str)

            # Ensure Evaluation_Criteria is a dictionary
            if isinstance(plan["Evaluation_Criteria"], list):
                plan["Evaluation_Criteria"] = {item["name"]: item["criteria"] for item in plan["Evaluation_Criteria"]}

            if verbose:
                print("Generated plan structure:")
                print(json.dumps(plan, indent=2))
                flush()
            return plan
        except json.JSONDecodeError:
            logging.error("Unable to parse JSON response from Gemini. Raw response:")
            print(response.text)
            flush()
            return None

    except Exception as e:
        logging.error(f"Error generating plan: {str(e)}")
        flush()
        return None

# Function to develop drafts (using GPT-4)
def develop_drafts(plan: Optional[Dict], goal: str) -> Dict[str, str]:
    if plan is None:
        logging.error("No plan available. Cannot develop drafts.")
        flush()
        return {}

    drafts = {}

    # Rate limiting parameters for OpenAI API
    requests_per_minute = 300 # Conservative estimate for GPT-4, adjust if needed
    delay_in_seconds = 60.0 / requests_per_minute

    if "Detailed_Outline" in plan and "Evaluation_Criteria" in plan:
        for step_item in plan["Detailed_Outline"]:
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

            try:
                response = openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": prompt}]
                )
                draft_content = response.choices[0].message.content
                drafts[step] = draft_content

                if verbose:
                    print(f"Draft for step '{step}':\n{draft_content}\n")
                    flush()

                # Introduce a delay to respect rate limits
                time.sleep(delay_in_seconds) 

            except Exception as e:
                logging.error(f"Error calling OpenAI API for step '{step}': {e}")
                flush()
    else:
        logging.error("'Detailed_Outline' or 'Evaluation_Criteria' missing in the plan. Cannot generate drafts.")
        flush()
    return drafts

# Function to generate revision requests
def generate_revision_requests(drafts, plan, original_goal):
    revision_requests = {}
    if not drafts or not plan:
        print("Error: No drafts or plan to process.")
        flush()
        return revision_requests

    # Rate limiting parameters for OpenAI API
    requests_per_minute = 300 # Conservative estimate for GPT-4, adjust if needed
    delay_in_seconds = 60.0 / requests_per_minute

    for step, draft in tqdm(drafts.items(), desc="Generating revision requests..."):
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

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}]
            )
            revision_requests[step] = response.choices[0].message.content
            if verbose:
                print(f"Revision request for step '{step}':\n{revision_requests[step]}\n")
                flush()

            # Introduce a delay to respect rate limits
            time.sleep(delay_in_seconds) 

        except Exception as e:
            print(f"Error calling OpenAI API for step '{step}': {e}")
            flush()

    return revision_requests

# Function to compile final plan
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

# Function to convert plan to Markdown
def convert_to_markdown(plan):
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
    for step, request in plan.revision_requests.items():
        md_content += f"### {step}\n\n{request}\n\n"
    md_content += "## Success Measures\n\n"
    for measure in plan.Success_Measures:
        md_content += f"- {measure}\n"
    return md_content

# Function to save file
def save_file(content, filename):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        logging.info(f"Saved {filename}")
    except IOError as e:
        logging.error(f"Error saving file {filename}: {e}")

# Function to save plan outputs (JSON and Markdown)
# Function to save plan outputs (JSON and Markdown)
def save_plan_outputs(plan: ProjectPlan):
    """
    This function saves the project plan as both JSON and Markdown
    It includes validation to ensure the plan conforms to the ProjectPlan schema
    """
    current_date = datetime.now().strftime("%Y-%m-%d")
    current_datetime = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    notebook_name = os.path.splitext(os.path.basename(__file__))[0]

    # Rest of the function stays the same...


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

# Main execution
with tee_output():  # Wrap the main execution block
    # Generate initial plan
    plan = generate_plan(project_goal)
    if plan is None:
        logging.warning("Failed to generate plan. Proceeding with an empty plan.")
        plan = {}

    # Develop drafts
    drafts = develop_drafts(plan, project_goal)

    # Generate revision requests
    revision_requests = generate_revision_requests(drafts, plan, project_goal)

    # Add the revision requests to the plan
    plan["revision_requests"] = revision_requests

    # Print revision requests for verification
    print("\nGenerated Revision Requests:")
    for step, request in revision_requests.items():
        print(f"\n{step}:\n{request}")
    flush()

    # Compile the final comprehensive project plan
    final_plan = compile_final_plan(drafts, plan, project_goal)

    print("Final Comprehensive Project Plan:")
    print(json.dumps(final_plan.model_dump(), indent=2))
    flush()

    # Save the plan outputs
    save_plan_outputs(final_plan)

    print(f"\nProject plan saved.")
    print("You can now run pre-notebook-15-Modular-FileInput-FIXED.py to start the iterative refinement process.")
    flush()