# Project: Distributed AI Project Manager (MVP)
**Stack:** Python | PydanticAI | Trello API  
**Goal:** Build an agentic pipeline that ingests unstructured "chaos" (transcripts, docs) and converts it into structured "order" (Trello Cards) with high schema compliance.

---

## 1. High-Level Architecture
We are building a **MapReduce Agent** system. It does not just "chat"; it processes information in parallel stages.

### The Flow (Conceptual)
1.  **Ingest (The Source):** Raw text (Meeting logs, PDF requirements, Slack dumps).
2.  **Map (The Workers):** Split text into chunks. Multiple `Extractor Agents` run in parallel to find potential tasks.
3.  **Reduce (The Manager):** A `Synthesis Agent` reviews all potential tasks, de-duplicates them, resolves conflicts (e.g., "Bob said due Friday" vs "Alice said due Monday"), and finalizes the list.
4.  **Execute (The Tool):** Validated JSON is pushed to the Trello API.

---

## 2. Phase 1: The Atomic Unit (Single Agent)
Before distributed processing, we must perfect the single-threaded logic.

**Objective:**
Take a single string input and reliably output a `TrelloCard` object (or list of objects) that adheres to strict validation rules.

### The Data Contract (Schema)
We do not pass strings. We pass these Pydantic Models:

**Input:** `RawText` (str)
**Output:** `TicketBatch` (List[TrelloCard])

| Field | Type | Validation Rule |
| :--- | :--- | :--- |
| `title` | `str` | Max 80 chars. Action-oriented (starts with Verb). |
| `description` | `str` | Must include context from source text. |
| `owner` | `str` | Must map to a known team member or "Unassigned". |
| `priority` | `Enum` | `Critical` (Same day), `High` (This sprint), `Low` (Backlog). |
| `labels` | `List[Enum]`| `Bug`, `Feature`, `Docs`, `TechDebt`. |

> **Self-Correction Policy:**
> If the LLM outputs a label that doesn't exist (e.g., "Urgent"), PydanticAI intercepts the error and re-prompts the LLM: *"Urgent is not a valid label. Choose from Bug, Feature, Docs..."*

---

## 3. Phase 2: The Distributed Logic (MapReduce)
Once Phase 1 works, we wrap it in the orchestration layer.

### The "Splitter" Strategy
* **Problem:** Context Window Limits.
* **Solution:** Semantic Chunking.
    * *Bad:* Split by character count (might cut a sentence in half).
    * *Good:* Split by "Topic Shift" or Paragraphs.

### The "Reducer" Strategy (Entity Resolution)
The Reducer Agent is the most complex. It must handle logic like:
* **De-duplication:** "Fix Login" (Page 1) == "Login bug" (Page 5).
* **Conflict Resolution:** If Source A says "Priority: High" and Source B says "Priority: Low", default to High (Safety First).

---

## 4. Testing Strategy (The Pyramid)
We do not test by "vibes." We test by assertions.

### Level 1: Unit Tests (No API Costs)
* **Tool Mocks:** Verify the `create_card` function constructs the correct URL/Payload.
* **Schema Tests:** Verify `TrelloCard` raises an error if `priority="Super High"`.

### Level 2: Functional Tests (Mocked LLM)
* Use `TestModel` (PydanticAI) to simulate LLM responses.
* **Scenario:** Inject a predefined "bad JSON" response and verify the Agent correctly retries/recovers.

### Level 3: Logic Tests (Real LLM / "The Gauntlet")
* **The Ambiguity Test:** Input: *"Make sure the thing works."* -> Expect: Error or request for clarification (Agent shouldn't guess).
* **The Conflict Test:** Input: *"Project is due Monday. Actually, make it Tuesday."* -> Expect: Due date = Tuesday.
