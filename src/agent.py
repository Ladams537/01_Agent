import os
from typing import Literal

import requests
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent

load_dotenv()

if not os.getenv("GEMINI_API_KEY"):
    raise ValueError("❌ Missing GEMINI_API_KEY in .env file")


class TrelloCard(BaseModel):
    title: str
    description: str
    tag: Literal["Bug", "Feature", "Docs"]


agent = Agent(
    "google-gla:gemini-2.5-flash-lite",
    output_type=TrelloCard,
    system_prompt="You are a Project Manager. "
    "Create a Trello card from the user request.",
)


def post_to_trello(card: TrelloCard):
    url = "https://api.trello.com/1/cards"
    query = {
        "key": os.getenv("TRELLO_API_KEY"),
        "token": os.getenv("TRELLO_TOKEN"),
        "idList": os.getenv("TRELLO_LIST_ID"),
        "name": f"[{card.tag}] {card.title}",
        "desc": card.description,
        "pos": "top",
    }

    response = requests.post(url, params=query)

    if response.status_code == 200:
        print("Trello card created successfully")
    else:
        print(f"Failed to create Trello card: {response.text}")


USER_DB = ["alice", "bob", "charlie"]


async def main():
    user_input = "The checkout button is 404ing on mobile devices!"

    # Phase 1: Think (Cheap/Fast)
    print("🤖 Thinking...")
    result = await agent.run(user_input)
    card_data = result.output

    # Phase 2: Execute (Real World)
    print(f"✅ Generated Plan: {card_data.title}")
    post_to_trello(card_data)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
