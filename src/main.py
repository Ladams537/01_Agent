import asyncio
import json
import os
from typing import List, Literal, Optional

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent

load_dotenv()


class TrelloCard(BaseModel):
    id: Optional[str] = None
    title: str
    description: str
    tag: Literal["Bug", "Feature", "Docs"]


class Plan(BaseModel):
    steps: List[str]
    reasoning: str


class ExecutionResult(BaseModel):
    output_data: Optional[TrelloCard] = None
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
    draft_card: Optional[TrelloCard] = None
    retry_count: int = 0
    max_retries: int = 3
    final_result: Optional[str] = None


def search_trello_tool(query: str) -> str:
    """Searches Trello for card on configured board."""
    print(f"   >>> [Tool Call] Searching Trello for: '{query}'...")

    url = "https://api.trello.com/1/search"
    params = {
        "key": os.getenv("TRELLO_API_KEY"),
        "token": os.getenv("TRELLO_API_TOKEN"),
        "query": query,
        "idBoards": os.getenv("TRELLO_BOARD_ID"),
        "modelTypes": "cards",
        "card_fields": "name,desc,idList",
        "card_limit": 5,
    }

    resp = requests.get(url, params)
    if resp.status_code == 200:
        cards = resp.json().get("cards", [])
        if not cards:
            return f"No cards found matching '{query}'."

        summary = []
        for c in cards:
            summary.append(
                f"CARD_ID: {c['id']} | TITLE: {c['name']} | DESC: {c['desc'][:50]}"
            )
        return "\n".join(summary)
    return f"Error searching Trello: {resp.text}"


planner_agent = Agent(
    "google-gla:gemini-2.5-flash-lite",
    output_type=Plan,
    tools=[search_trello_tool],
    system_prompt=(
        "You are a Project Manager. "
        "1. ALWAYS search Trello first. Use simple keywords (e.g., 'login' instead of 'fix login bug'). "
        "2. If a card is found, your plan MUST be: 'Update card [INSERT_EXACT_ID_HERE]'. "
        "   Example: 'Update card 60d5ec...' "
        "3. If no card is found, your plan is to 'Create a new card'. "
        "4. Include all details (assignee, priority) in the plan steps."
    ),
)


executor_agent = Agent(
    "google-gla:gemini-2.5-flash-lite",
    output_type=TrelloCard,
    system_prompt=(
        "You are a Task Drafter. "
        "Rules:"
        "1. If the plan mentions a specific Card ID (alphanumeric like 60d5...), put it in the 'id' field."
        "2. NEVER put a person's name (like 'Shelley') in the 'id' field."
        "3. If the plan says 'Create new', leave 'id' as None."
        "4. Put assignee names and priority levels in the 'description' field."
    ),
)


async def run_planner(state: AgentState) -> Plan:
    print(f"--- [Planner] Thinking about: {state.input_query} ---")

    result = await planner_agent.run(state.input_query)

    plan = result.output

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

    if not draft:
        return Evaluation(decision="reject", critique="No card was generated.")

    if draft.id:
        if len(draft.id) < 10 or " " in draft.id:
            return Evaluation(
                decision="reject",
                critique=f"The ID '{draft.id}' is invalid. It looks like a name or title. "
                "The ID must be the alphanumeric hash found by the Search tool.",
            )

    return Evaluation(decision="approve")


async def run_committer(state: AgentState):
    print("--- [Committer] 🚀 Push to Trello API ---")

    card = state.draft_card
    if not card:
        return "Error: No card to commit"

    base_params = {
        "key": os.getenv("TRELLO_API_KEY"),
        "token": os.getenv("TRELLO_TOKEN"),
    }

    # LOGIC BRANCH: UPDATE vs CREATE
    if card.id:
        # --- UPDATE EXISTING CARD ---
        print(f"   Action: Updating Card {card.id}")
        url = f"https://api.trello.com/1/cards/{card.id}"
        query = {
            **base_params,
            "name": f"[{card.tag}] {card.title}",
            "desc": card.description,
        }
        response = requests.put(url, params=query)  # PUT
        action_type = "Updated"

    else:
        # --- CREATE NEW CARD ---
        print("   Action: Creating New Card")
        url = "https://api.trello.com/1/cards"
        query = {
            **base_params,
            "idList": os.getenv("TRELLO_LIST_ID"),
            "name": f"[{card.tag}] {card.title}",
            "desc": card.description,
            "pos": "top",
        }
        response = requests.post(url, params=query)  # POST
        action_type = "Created"

    if response.status_code == 200:
        return f"SUCCESS: {action_type} card {response.json().get('shortUrl')}"
    else:
        return f"API ERROR: {response.text}"


async def run_workflow(user_query: str):
    state = AgentState(input_query=user_query)
    current_plan = None

    print(f"Starting Workflow: {user_query}")

    while state.current_step not in ["done", "failed"]:
        if state.current_step == "planning":
            current_plan = await run_planner(state)
            state.current_step = "executing"

        elif state.current_step == "executing":
            if not current_plan:
                raise ValueError("No Plan!")

            exec_result = await run_executor(state, current_plan)

            if exec_result.success:
                state.draft_card = exec_result.output_data
                state.current_step = "evaluating"
            else:
                state.retry_count += 1
                print(f"Executor Crashed: {exec_result.error_message}")
                if state.retry_count > state.max_retries:
                    state.current_step = "failed"

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
                    state.scratchpad.append(evaluation.critique)

                if state.retry_count > state.max_retries:
                    print("!!! MAX RETRIES REACHED !!!")
                    state.current_step = "failed"
                    state.final_result = "Human Handoff Required"
                else:
                    state.current_step = "executing"

        elif state.current_step == "committing":
            result_msg = await run_committer(state)
            state.final_result = result_msg
            state.current_step = "done"

    return state


async def main():
    final_state = await run_workflow("Update the login bug.")
    print(f"\nFINAL RESULT: {final_state.final_result}")


if __name__ == "__main__":
    asyncio.run(main())
