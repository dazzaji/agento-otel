# RUN: python3 1_01_UserInputGoal-to-ExportPlanStructure_MoreLogging-RigorousPrompt.py

# Possible goal: I need to create a new python script to set up creative collaborative LLM-based agents design that uses OpenAI, Anthropic, and Google APIs for their most powerful LLMs in a way that the agents chat with each other to work on achieving a goal that the user inputs. The system brainstorms potential approachs and choose the best (based on criteria the LLM's generate in the context of the goal) and then develops a plan, then implements and refines the plan until it is finalized. Then the plan guides generation, refinements, and finalization of the final output. Each stage of development is created, refined, and agreed as finished by the 3 agents and is then exported as JSON and Markdown for the user to have.

# Note: Before running this script, ensure I have installed the dependencies and required libraries:
# python -m pip install -U openai google-generativeai python-dotenv tqdm pydantic
# python -m pip install -r requirements.txt

# RUN: python3 1_01_UserInputGoal-to-ExportPlanStructure_MoreLogging-RigorousPrompt.py

import os
from dotenv import load_dotenv
import json
import sys
import subprocess
from typing import Dict, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import logging
import google.generativeai as genai
import typing_extensions as typing
from google.api_core import retry
from google.api_core import exceptions as google_exceptions
import contextlib

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

# Load environment variables
load_dotenv()

# Function to flush the stdout buffer
def flush():
    sys.stdout.flush()

# Access API key from environment variables
gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    raise ValueError("GEMINI_API_KEY not found in environment variables")

# Initialize Gemini client
genai.configure(api_key=gemini_api_key)

# Define Pydantic model for plan structure validation
class PlanStructure(BaseModel):
    """
    Model for validating the initial plan structure that will be exported to JSON.
    """
    Title: str
    Overall_Summary: str
    Original_Goal: str
    Detailed_Outline: list[Dict[str, str]] = Field(..., description="List of steps with content")
    Evaluation_Criteria: Dict[str, str] = Field(..., description="Criteria for evaluating each step")
    Success_Measures: list[str]

def generate_plan_structure(goal: str) -> Optional[Dict]:
    """
    Generates the initial plan structure using Gemini Pro.
    Returns a validated plan structure dictionary or None if generation fails.
    """
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

    Ensure that:
    1. Each step in the "Detailed_Outline" has a corresponding entry in the "Evaluation_Criteria"
    2. The Original_Goal field contains the exact goal provided
    3. Content is comprehensive but concise
    4. The response is valid JSON only, with no additional text or explanations

    Your response must be valid JSON and nothing else. Do not include any explanations or text outside of the JSON structure.
    """

    print("\nGenerating plan structure...", flush=True)

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
    ), deadline=90)
    def generate_with_retry():
        return model.generate_content(prompt)

    try:
        response = generate_with_retry()
        
        # Extract JSON from response
        json_start = response.text.find('{')
        json_end = response.text.rfind('}') + 1
        json_str = response.text[json_start:json_end]
        
        # Parse and validate JSON structure
        plan = json.loads(json_str)
        
        # Ensure Evaluation_Criteria is a dictionary
        if isinstance(plan["Evaluation_Criteria"], list):
            plan["Evaluation_Criteria"] = {item["name"]: item["criteria"] for item in plan["Evaluation_Criteria"]}
        
        # Validate using Pydantic model
        validated_plan = PlanStructure(**plan)
        return validated_plan.model_dump()

    except Exception as e:
        logging.error(f"Error generating or validating plan structure: {str(e)}")
        return None

def save_plan_structure(plan: Dict, base_filename: str = "plan_structure") -> bool:
    """
    Saves the plan structure to JSON files (both with and without timestamp).
    Returns True if successful, False otherwise.
    """
    try:
        # Save with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        timestamped_filename = f"{base_filename}_{timestamp}.json"
        
        # Save both versions
        for filename in [f"{base_filename}.json", timestamped_filename]:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(plan, f, indent=2)
            logging.info(f"Saved plan structure to {filename}")
        
        return True
    except Exception as e:
        logging.error(f"Error saving plan structure: {str(e)}")
        return False

def main():
    """
    Main execution function that handles user input and manages the plan structure generation process.
    """
    with tee_output():
        print("\nWelcome to the Project Plan Structure Generator!")
        print("This module will help you create the initial plan structure for your project.")
        print("\nPlease enter your project goal below.")
        print("TIP: Be as specific as possible to get the best results.")
        flush()

        # Get user input
        project_goal = input("\nProject Goal: ").strip()
        if not project_goal:
            print("Error: Project goal cannot be empty.")
            return

        # Generate plan structure
        plan = generate_plan_structure(project_goal)
        if not plan:
            print("Error: Failed to generate plan structure. Please try again.")
            return

        # Display generated plan
        print("\nGenerated Plan Structure:")
        print(json.dumps(plan, indent=2))
        flush()

        # Save plan structure
        if save_plan_structure(plan):
            print("\nPlan structure has been successfully saved!")
            print("You can now run 1_Goal-to-Plan-Stable-SlightWednesdayFix.py to develop the full plan.")
        else:
            print("\nError: Failed to save plan structure.")
        flush()

if __name__ == "__main__":
    main()