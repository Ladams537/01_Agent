import os

import requests
from dotenv import load_dotenv

load_dotenv()

KEY = os.getenv("TRELLO_API_KEY")
TOKEN = os.getenv("TRELLO_TOKEN")

if not KEY or not TOKEN:
    print("❌ Error: Missing Trello credentials in .env")
    exit()

# 1. Get your username
me_url = f"https://api.trello.com/1/members/me?key={KEY}&token={TOKEN}"
user_id = requests.get(me_url).json()["id"]

# 2. Get your boards
boards_url = (
    f"https://api.trello.com/1/members/{user_id}/boards?key={KEY}&token={TOKEN}"
)
boards = requests.get(boards_url).json()

print("\n📊 YOUR BOARDS:")
for idx, b in enumerate(boards):
    print(f"{idx}: {b['name']} (ID: {b['id']})")

board_idx = int(input("\nSelect a board number to scan: "))
board_id = boards[board_idx]["id"]

# 3. Get Lists on that board
lists_url = f"https://api.trello.com/1/boards/{board_id}/lists?key={KEY}&token={TOKEN}"
lists = requests.get(lists_url).json()

print(f"\n📝 LISTS ON '{boards[board_idx]['name']}':")
for l in lists:
    print(f"Name: {l['name']} | ID: {l['id']}")

print("\n✅ COPY the 'ID' above into your .env file as TRELLO_LIST_ID")
