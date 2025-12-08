import asyncio
import json
import os
from typing import Any, Dict, List, Literal, Optional

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent

load_dotenv()

# --- 1. DEFINE DATA MODELS ---


# The "Draft" Object
class TrelloCard(BaseModel):
    title: str
    description: str
    tag: Literal["Bug", "Feature", "Docs"]


class Plan(BaseModel):
    steps: List[str]
    reasoning: str


class ExecutionResult(BaseModel):
    output_data: Optional[TrelloCard] = None  # Holds our draft
    success: bool
    error_message: Optional[str] = None


class Evaluation(BaseModel):
    decision: Literal["approve", "reject"]
    critique: Optional[str] = None


class AgentState(BaseModel):
    input_query: str
    current_step: Literal[
        "planning", "executing", "evaluating", "committing", "done", "failed"
    ] = "planning"
    scratchpad: List[str] = Field(default_factory=list)
    draft_card: Optional[TrelloCard] = None  # Store the safe draft here
    retry_count: int = 0
    max_retries: int = 3
    final_result: Optional[str] = None


# --- 2. THE AGENT (GENERATOR ONLY) ---

# Notice: No tools here! Just pure data generation.
planner_agent = Agent(
    "google-gla:gemini-2.5-flash-lite",
    output_type=Plan,
    system_prompt=(
        "You are a Project Manager. Break down the user's request into clear, logical steps. "
        "Your plan must be actionable."
    ),
)

executor_agent = Agent(
    "google-gla:gemini-2.5-flash-lite",
    output_type=TrelloCard,
    system_prompt="You are a Task Drafter. Create a TrelloCard object based on the user request and plan.",
)

# --- 3. THE WORKFLOW NODES ---


async def run_planner(state: AgentState) -> Plan:
    print(f"--- [Planner] Thinking about: {state.input_query} ---")

    # We ask the LLM to generate the plan dynamically
    result = await planner_agent.run(state.input_query)

    # PydanticAI automatically validates the output against the Plan model
    plan = result.output

    # Optional: Print the reasoning to see the brain working
    print(f"    > Reasoning: {plan.reasoning}")
    print(f"    > Steps: {plan.steps}")

    return plan


async def run_executor(state: AgentState, current_plan: Plan) -> ExecutionResult:
    print(f"--- [Executor] Drafting Card... (Attempt {state.retry_count + 1}) ---")

    prompt = f"""
    Create a Trello Card for this request: "{state.input_query}"
    Follow this plan: {current_plan.steps}
    Context: {current_plan.reasoning}
    """

    if state.scratchpad:
        prompt += f"""
        !!! PREVIOUS ATTEMPTS FAILED !!!
        The Evaluator rejected your previous work with this feedback:
        {json.dumps(state.scratchpad, indent=2)}

        STRICT INSTRUCTION: You must fix these issues in your new draft.
        """

    try:
        result = await executor_agent.run(prompt)
        return ExecutionResult(output_data=result.output, success=True)

    except Exception as e:
        return ExecutionResult(success=False, error_message=str(e))


async def run_evaluator(state: AgentState) -> Evaluation:
    print("--- [Evaluator] Checking Draft... ---")

    draft = state.draft_card

    # 1. Sanity Check
    if not draft:
        return Evaluation(decision="reject", critique="No card was generated.")

    # 2. Rule Check (Example: Description must be detailed)
    if len(draft.description) < 10:
        return Evaluation(
            decision="reject",
            critique="Description is too short. Please provide more detail.",
        )

    # 3. Tag Check
    if draft.tag not in ["Bug", "Feature", "Docs"]:
        return Evaluation(decision="reject", critique="Invalid Tag.")

    return Evaluation(decision="approve")


async def run_committer(state: AgentState):
    """The only place where side effects happen."""
    print("--- [Committer] 🚀 Push to Trello API ---")

    card = state.draft_card
    if not card:
        return "Error: No card to commit"

    url = "https://api.trello.com/1/cards"
    query = {
        "key": os.getenv("TRELLO_API_KEY"),
        "token": os.getenv("TRELLO_TOKEN"),
        "idList": os.getenv("TRELLO_LIST_ID"),
        "name": f"[{card.tag}] {card.title}",
        "desc": card.description,
        "pos": "top",
    }

    # REAL API CALL
    response = requests.post(url, params=query)

    if response.status_code == 200:
        return f"SUCCESS: Created card {response.json().get('shortUrl')}"
    else:
        return f"API ERROR: {response.text}"


# --- 4. THE ORCHESTRATOR ---


async def run_workflow(user_query: str):
    state = AgentState(input_query=user_query)
    current_plan = None

    print(f"Starting Workflow: {user_query}")

    while state.current_step not in ["done", "failed"]:
        # --- PHASE 1: PLAN ---
        if state.current_step == "planning":
            current_plan = await run_planner(state)
            state.current_step = "executing"

        # --- PHASE 2: EXECUTE (DRAFT) ---
        elif state.current_step == "executing":
            if not current_plan:
                raise ValueError("No Plan!")

            exec_result = await run_executor(state, current_plan)

            if exec_result.success:
                state.draft_card = exec_result.output_data
                state.current_step = "evaluating"
            else:
                # Agent crashed generation
                state.retry_count += 1
                print(f"Executor Crashed: {exec_result.error_message}")
                if state.retry_count > state.max_retries:
                    state.current_step = "failed"

        # --- PHASE 3: EVALUATE ---
        elif state.current_step == "evaluating":
            evaluation = await run_evaluator(state)

            if evaluation.decision == "approve":
                print(">>> Evaluator Approved. Moving to Commit.")
                state.current_step = "committing"
            else:
                state.retry_count += 1
                print(f">>> Evaluator Rejected: {evaluation.critique}")
                if evaluation.critique is None:
                    print("No critique provided")
                else:
                    state.scratchpad.append(
                        evaluation.critique
                    )  # Pass feedback to history

                if state.retry_count > state.max_retries:
                    print("!!! MAX RETRIES REACHED !!!")
                    state.current_step = "failed"
                    state.final_result = "Human Handoff Required"
                else:
                    # Loop back to try drafting again
                    state.current_step = "executing"

        # --- PHASE 4: COMMIT (SIDE EFFECT) ---
        elif state.current_step == "committing":
            result_msg = await run_committer(state)
            state.final_result = result_msg
            state.current_step = "done"

    return state


# --- MAIN ---


async def main():
    final_state = await run_workflow("Fix the login page crashing on iOS")
    print(f"\nFINAL RESULT: {final_state.final_result}")


if __name__ == "__main__":
    asyncio.run(main())
