AI-Powered E-Commerce Customer Support Agent — Backend Architecture
> **Document Type:** Production Backend Blueprint
> **Version:** 2.0 — Elite Upgrade (5 critical improvements applied)
> **Stack:** FastAPI · Groq (llama3-70b-8192) · LangChain · MySQL · PageIndex RAG
> **Frontend:** Chatur UI (existing — not redesigned)
> **Changelog v2.0:**
> 1. ✅ Context Filter Layer added before LLM (token safety)
> 2. ✅ Intent confidence scoring + LLM fallback classifier
> 3. ✅ Transaction safety in Action Executor
> 4. ✅ Stronger behavior signals in Learning System → injected into Decision Engine
> 5. ✅ LLM retry + timeout strategy
> 6. ✅ `trace_id` added to logging for full request traceability
> 7. ✅ `asyncio.wait_for` timeout guards on all external calls
---
Table of Contents
System Overview
Full Pipeline (Step-by-Step)
Context Design
Context Filter Layer ⭐ NEW
Intent System
Decision Engine
LLM Integration (Groq)
Action Executor
Learning System
Database Design
Async & Scalability
Error Handling & Fallbacks
Logging (Simple & Minimal)
Frontend Integration (Chatur UI)
Final End-to-End Flow
---
1. System Overview
1.1 What This System Does
This is a production-grade AI customer support backend for an e-commerce platform. It handles customer queries across orders, returns, complaints, and product information. The system integrates an LLM (Groq) strictly for natural language generation — all business decisions are made deterministically by a rule-based engine.
1.2 Goals
Handle customer queries reliably without hallucinated decisions
Support multi-modal input: text, images, and PDFs
Provide context-aware responses using RAG (PageIndex) and MySQL memory
Learn from past interactions to improve routing and responses over time
Remain fast, async, and scalable for 1–100 concurrent users
1.3 Key Design Principles
Principle	Implementation
Deterministic decisions	All business logic is rule-based; LLM never decides
Modular pipeline	Each stage (intent, decision, LLM, executor) is independent
Minimal LLM surface	Groq is called exactly once per request, for message generation only
Context-first	Every LLM call is preceded by a fully structured context object
Async-native	FastAPI async throughout; no blocking I/O
---
2. Full Pipeline (Step-by-Step)
```
User Input
    │
    ▼
[1] Input Layer          → Normalize text/image/PDF to unified InputPayload
    │
    ▼
[2] Context Builder      → Fetch MySQL data + RAG knowledge + session memory
    │
    ▼
[3] Intent Router        → Classify primary intent + sub-intent (rule-based)
    │
    ▼
[4] Decision Engine      → Apply business rules → produce allowed_actions + priority_action
    │
    ▼
[5] LLM Layer (Groq)     → Generate human message using context + decision (JSON output)
    │
    ▼
[6] Action Executor      → Validate + execute DB action (idempotent)
    │
    ▼
[7] Learning System      → Log outcome + extract behavior signal
    │
    ▼
Response → Chatur UI
```
---
2.1 Input Layer
Responsibility: Accept multi-modal input from the Chatur UI and normalize it into a single `InputPayload`.
Supported input types:
Type	Source	Processing
Text	Chat input	Pass-through
Image	File upload (JPEG/PNG)	Convert to base64; extract text via OCR if needed
PDF	File upload	Extract text using `pdfplumber`
Voice	Browser speech-to-text (Chatur handles)	Arrives as text string to `/chat`
Output:
```python
@dataclass
class InputPayload:
    user_id: str
    session_id: str
    text: str                    # normalized text (from any input type)
    raw_attachments: list[dict]  # [{type: "image"|"pdf", content: <base64/text>}]
    timestamp: str
```
Endpoint:
```python
@app.post("/chat")
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    payload = await InputProcessor.process(request)
    ...
```
---
2.2 Context Builder
Responsibility: Assemble everything the Decision Engine and LLM need, in one structured `Context` object.
Sources fetched in parallel (asyncio.gather):
Data	Source	Field
User profile + order history	MySQL `users`, `orders`	`facts`
Policy knowledge	PageIndex RAG	`knowledge`
Past interactions	MySQL `learning_logs`	`memory`
Uploaded content	Parsed attachments	`evidence`
Business rules	Hardcoded config	`rules`
```python
async def build_context(payload: InputPayload) -> Context:
    user_data, rag_results, memory = await asyncio.gather(
        fetch_user_data(payload.user_id),
        query_pageindex(payload.text),
        fetch_memory(payload.user_id)
    )
    return Context(
        task=payload.text,
        facts=user_data,
        rules=BUSINESS_RULES,
        knowledge=rag_results,
        evidence=payload.raw_attachments,
        memory=memory,
        decision=None  # filled by Decision Engine
    )
```
---
2.3 Intent Router
Responsibility: Classify the user's primary intent and sub-intent using rule-based keyword and pattern matching. No ML classification.
Logic order:
Check for exact keyword patterns (regex)
Match against intent keyword map
Assign primary + sub-intent
Flag as `deterministic` or `conversational` mode
```python
def route_intent(text: str) -> IntentResult:
    text_lower = text.lower()
    for intent, patterns in INTENT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return IntentResult(
                    primary=intent.primary,
                    sub=intent.sub,
                    mode=intent.mode
                )
    return IntentResult(primary="UNKNOWN", sub=None, mode="conversational")
```
---
2.4 Decision Engine
Responsibility: Apply business rules to produce `allowed_actions` and `priority_action`. This is the only place business decisions are made.
Rules are applied deterministically based on: intent + order state + user history + policy + memory signals.
The output of the Decision Engine is written into `context.decision` before the LLM is called.
---
2.5 LLM Layer (Groq)
Responsibility: Generate a natural language message and confirm the action. Called once per request. Never makes business decisions.
Input: Full structured `Context` as JSON string in system prompt.
Output: Strict JSON `{"action": "...", "message": "..."}`.
---
2.6 Action Executor
Responsibility: Validate LLM's chosen action against `allowed_actions`, execute the corresponding DB function idempotently, and log success/failure.
---
2.7 Learning System
Responsibility: Store the outcome of each interaction and extract behavior signals that influence future decisions.
---
3. Context Design
Every LLM call receives a single structured JSON context. This ensures the LLM has complete, bounded information and cannot invent facts.
3.1 Full Context Schema
```json
{
  "task": "string",
  "facts": {
    "user_id": "string",
    "user_name": "string",
    "email": "string",
    "order_id": "string",
    "order_status": "string",
    "order_date": "string",
    "delivery_date": "string",
    "items": ["string"],
    "payment_status": "string",
    "return_window_days": 30,
    "past_returns": 0,
    "account_flags": []
  },
  "rules": {
    "return_window_days": 30,
    "max_returns_per_month": 3,
    "auto_refund_threshold_inr": 500,
    "refund_methods": ["original_payment", "store_credit"],
    "escalation_triggers": ["abusive", "legal_threat", "repeat_complaint"]
  },
  "knowledge": [
    {
      "source": "pageindex",
      "content": "string",
      "relevance_score": 0.95
    }
  ],
  "evidence": [
    {
      "type": "image|pdf",
      "extracted_text": "string",
      "summary": "string"
    }
  ],
  "memory": {
    "recent_intents": ["ORDER_STATUS", "RETURN_REQUEST"],
    "past_actions": ["refund_issued", "escalated"],
    "complaint_count": 2,
    "sentiment_trend": "negative",
    "vip_flag": false
  },
  "decision": {
    "intent": "RETURN_REQUEST",
    "sub_intent": "damaged_item",
    "allowed_actions": ["approve_return", "request_evidence", "escalate"],
    "priority_action": "approve_return",
    "reason": "Within return window. Damage evidence present. No prior abuse.",
    "deterministic": true
  }
}
```
3.2 Field Explanations
Field	Purpose	Source
`task`	The customer's original query in plain text	Input Layer
`facts`	Ground truth about the customer and their order	MySQL `users`, `orders`
`rules`	Business policy limits and conditions	Hardcoded config / `product_policies` table
`knowledge`	Relevant policy or FAQ content	PageIndex RAG
`evidence`	Parsed content from uploaded files	Attachment processor
`memory`	History of this customer's behavior	MySQL `learning_logs`
`decision`	The result of the Decision Engine (action + reasoning)	Decision Engine
---
4. Intent System
4.1 Full Intent Map
```python
INTENT_MAP = {
    "ORDER_STATUS": {
        "sub_intents": ["tracking", "delayed", "missing", "delivered_not_received"],
        "patterns": [r"where.*order", r"track.*order", r"order.*status", r"when.*deliver"],
        "mode": "deterministic"
    },
    "RETURN_REQUEST": {
        "sub_intents": ["damaged_item", "wrong_item", "changed_mind", "quality_issue"],
        "patterns": [r"return", r"send.*back", r"exchange", r"replacement"],
        "mode": "deterministic"
    },
    "REFUND_STATUS": {
        "sub_intents": ["refund_delay", "refund_not_received", "partial_refund"],
        "patterns": [r"refund", r"money back", r"credit.*back"],
        "mode": "deterministic"
    },
    "COMPLAINT": {
        "sub_intents": ["delivery_issue", "product_quality", "customer_service", "billing"],
        "patterns": [r"complaint", r"unhappy", r"terrible", r"worst", r"escalat"],
        "mode": "escalation_check"
    },
    "PRODUCT_QUERY": {
        "sub_intents": ["availability", "specs", "compatibility", "pricing"],
        "patterns": [r"product", r"item", r"available", r"spec", r"price"],
        "mode": "conversational"
    },
    "CANCELLATION": {
        "sub_intents": ["before_ship", "after_ship"],
        "patterns": [r"cancel", r"stop.*order"],
        "mode": "deterministic"
    },
    "PAYMENT_ISSUE": {
        "sub_intents": ["double_charge", "payment_failed", "coupon_issue"],
        "patterns": [r"charged twice", r"payment fail", r"coupon", r"discount"],
        "mode": "deterministic"
    },
    "UNKNOWN": {
        "sub_intents": [],
        "patterns": [],
        "mode": "conversational"
    }
}
```
4.2 Routing Logic
```
1. Normalize input text (lowercase, strip punctuation)
2. Iterate INTENT_MAP in priority order
3. First regex match wins → assign primary + sub_intent
4. If no match → UNKNOWN → conversational mode
5. Pass IntentResult to Decision Engine
```
4.3 Deterministic vs Conversational Mode
Mode	When Used	LLM Role
`deterministic`	Clear transactional intent (return, cancel, order status)	Generate message only
`conversational`	Product queries, vague requests, general FAQs	Generate message + suggest action
`escalation_check`	Complaints — check memory before routing	Escalate or handle
---
5. Decision Engine
5.1 Rule-Based Logic
```python
def run_decision_engine(intent: IntentResult, context: Context) -> Decision:
    facts = context.facts
    rules = context.rules
    memory = context.memory

    if intent.primary == "RETURN_REQUEST":
        days_since_delivery = compute_days(facts["delivery_date"])
        
        if days_since_delivery > rules["return_window_days"]:
            return Decision(
                allowed_actions=["deny_return"],
                priority_action="deny_return",
                reason="Outside return window"
            )
        
        if memory["complaint_count"] > 5 and memory["past_actions"].count("refund_issued") > 3:
            return Decision(
                allowed_actions=["escalate"],
                priority_action="escalate",
                reason="Flagged account — high abuse risk"
            )
        
        if has_evidence(context.evidence):
            return Decision(
                allowed_actions=["approve_return", "escalate"],
                priority_action="approve_return",
                reason="Evidence present, within window"
            )
        
        return Decision(
            allowed_actions=["request_evidence", "approve_return"],
            priority_action="request_evidence",
            reason="No evidence uploaded yet"
        )

    if intent.primary == "ORDER_STATUS":
        return Decision(
            allowed_actions=["provide_tracking"],
            priority_action="provide_tracking",
            reason="Informational query"
        )

    if intent.primary == "COMPLAINT":
        if any(t in context.task.lower() for t in rules["escalation_triggers"]):
            return Decision(
                allowed_actions=["escalate"],
                priority_action="escalate",
                reason="Escalation keyword detected"
            )
        return Decision(
            allowed_actions=["log_complaint", "escalate"],
            priority_action="log_complaint",
            reason="Standard complaint"
        )

    return Decision(
        allowed_actions=["provide_information"],
        priority_action="provide_information",
        reason="General query"
    )
```
5.2 Allowed Actions Reference
Action	Trigger Condition
`approve_return`	Within window + evidence present
`deny_return`	Outside return window
`request_evidence`	Within window, no evidence
`provide_tracking`	Order status intent
`issue_refund`	Approved return + refundable payment
`escalate`	Complaint trigger / abuse flag / legal threat
`log_complaint`	Standard complaint
`provide_information`	Product/general query
`cancel_order`	Cancellation before shipment
5.3 Memory Influence on Decisions
```python
# Escalate early for high-complaint users
if memory["complaint_count"] >= 3:
    priority_action = "escalate"

# Fast-track for VIP users
if memory["vip_flag"]:
    priority_action = "approve_return"  # skip evidence requirement

# Downgrade trust for refund abuse
if memory["past_actions"].count("refund_issued") > rules["max_returns_per_month"]:
    allowed_actions = ["escalate"]
    priority_action = "escalate"
```
---
6. LLM Integration (Groq)
6.1 System Prompt
```
You are a customer support assistant for an e-commerce platform.

You will receive a structured JSON context containing the customer's query, 
their order facts, business rules, knowledge base results, and a pre-made 
decision from the backend system.

YOUR ONLY JOB:
1. Choose an action from the "allowed_actions" list in context.decision
2. Write a clear, helpful, empathetic customer-facing message

HARD RULES:
- You MUST choose from allowed_actions only. Never invent actions.
- You MUST NOT override or ignore the priority_action unless there is an 
  explicit reason in the evidence field.
- You MUST NOT make business decisions (refund amounts, policy exceptions).
- Output ONLY valid JSON. No markdown, no preamble.

OUTPUT FORMAT:
{
  "action": "<one of allowed_actions>",
  "message": "<customer-facing message in plain English>"
}
```
6.2 API Call
```python
from groq import AsyncGroq

groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)

async def call_groq(context: Context) -> LLMOutput:
    context_json = json.dumps(context.dict(), indent=2)
    
    response = await groq_client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context_json}
        ],
        temperature=0.2,          # low temp for consistency
        max_tokens=512,
        response_format={"type": "json_object"}
    )
    
    raw = response.choices[0].message.content
    parsed = json.loads(raw)
    
    # Validate action is in allowed_actions
    if parsed["action"] not in context.decision.allowed_actions:
        parsed["action"] = context.decision.priority_action
    
    return LLMOutput(action=parsed["action"], message=parsed["message"])
```
6.3 Output Validation
```python
def validate_llm_output(output: dict, allowed_actions: list[str]) -> dict:
    if "action" not in output or "message" not in output:
        raise ValueError("Invalid LLM output structure")
    if output["action"] not in allowed_actions:
        output["action"] = allowed_actions[0]  # fallback to priority
    if len(output["message"]) < 5:
        raise ValueError("Empty message from LLM")
    return output
```
---
7. Action Executor
7.1 Action-to-Function Map
```python
ACTION_MAP = {
    "approve_return":      execute_approve_return,
    "deny_return":         execute_deny_return,
    "request_evidence":    execute_request_evidence,
    "provide_tracking":    execute_provide_tracking,
    "issue_refund":        execute_issue_refund,
    "escalate":            execute_escalate,
    "log_complaint":       execute_log_complaint,
    "provide_information": execute_provide_information,
    "cancel_order":        execute_cancel_order,
}

async def execute_action(action: str, context: Context) -> ActionResult:
    if action not in ACTION_MAP:
        return ActionResult(success=False, error="Unknown action")
    
    fn = ACTION_MAP[action]
    result = await fn(context)
    return result
```
7.2 Idempotency
All write operations check for prior execution to prevent duplicate DB changes:
```python
async def execute_approve_return(context: Context) -> ActionResult:
    order_id = context.facts["order_id"]
    
    # Idempotency check
    existing = await db.fetchone(
        "SELECT id FROM orders WHERE order_id = %s AND status = 'return_approved'",
        (order_id,)
    )
    if existing:
        return ActionResult(success=True, note="Already approved — idempotent")
    
    await db.execute(
        "UPDATE orders SET status = 'return_approved', updated_at = NOW() WHERE order_id = %s",
        (order_id,)
    )
    return ActionResult(success=True)
```
7.3 Action Implementations (Signatures)
```python
async def execute_provide_tracking(context: Context) -> ActionResult:
    # Read-only: fetch tracking info from orders table
    ...

async def execute_issue_refund(context: Context) -> ActionResult:
    # Write: update orders.refund_status; check amount threshold
    ...

async def execute_escalate(context: Context) -> ActionResult:
    # Write: insert into escalations table; notify support team
    ...

async def execute_cancel_order(context: Context) -> ActionResult:
    # Write: update orders.status = 'cancelled' if status = 'pending'
    ...
```
---
8. Learning System
8.1 What Gets Logged
After every successful or failed action, the learning system records:
```python
@dataclass
class LearningRecord:
    user_id: str
    intent: str
    sub_intent: str
    action_taken: str
    outcome: str          # "success" | "failure" | "escalated"
    sentiment: str        # "positive" | "neutral" | "negative" (from message heuristic)
    timestamp: str
```
8.2 MySQL Schema
```sql
CREATE TABLE learning_logs (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       VARCHAR(20)  NOT NULL,
    intent        VARCHAR(50),
    sub_intent    VARCHAR(50),
    action_taken  VARCHAR(50),
    outcome       VARCHAR(20),
    sentiment     VARCHAR(20),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_intent (intent)
);
```
8.3 Behavior Signals (Aggregated)
These signals are computed on read (when building context.memory):
```python
async def fetch_memory(user_id: str) -> dict:
    rows = await db.fetchall(
        """
        SELECT intent, action_taken, outcome, sentiment
        FROM learning_logs
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (user_id,)
    )
    return {
        "recent_intents": [r["intent"] for r in rows[:5]],
        "past_actions": [r["action_taken"] for r in rows],
        "complaint_count": sum(1 for r in rows if r["intent"] == "COMPLAINT"),
        "refund_count": sum(1 for r in rows if r["action_taken"] == "issue_refund"),
        "sentiment_trend": compute_sentiment_trend(rows),
        "vip_flag": await check_vip_status(user_id)
    }
```
8.4 How Learning Influences Decisions
Signal	Effect on Decision Engine
`complaint_count >= 3`	Earlier escalation trigger
`refund_count > max`	Block further refunds, route to human
`sentiment_trend = negative`	Softer, more empathetic message prompt
`vip_flag = true`	Skip evidence requirement for returns
`recent_intents` repeated	Detect unresolved issue, prioritize escalation
---
9. Database Design
9.1 `users`
```sql
CREATE TABLE users (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      VARCHAR(20)   UNIQUE NOT NULL,
    name         VARCHAR(100),
    email        VARCHAR(150),
    phone        VARCHAR(20),
    vip_flag     BOOLEAN DEFAULT FALSE,
    account_status VARCHAR(20) DEFAULT 'active',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
9.2 `orders`
```sql
CREATE TABLE orders (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    order_id        VARCHAR(30) UNIQUE NOT NULL,
    user_id         VARCHAR(20) NOT NULL,
    status          VARCHAR(30),         -- pending, shipped, delivered, return_approved, cancelled
    payment_status  VARCHAR(20),
    refund_status   VARCHAR(20),
    items           JSON,
    order_date      DATE,
    delivery_date   DATE,
    tracking_number VARCHAR(50),
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```
9.3 `product_policies`
```sql
CREATE TABLE product_policies (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    policy_key      VARCHAR(50) UNIQUE NOT NULL,
    policy_value    TEXT,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Example rows:
-- ('return_window_days', '30')
-- ('max_returns_per_month', '3')
-- ('auto_refund_threshold_inr', '500')
```
9.4 `learning_logs`
(See Section 8.2 above)
9.5 `logs` (Operational Logs)
```sql
CREATE TABLE logs (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    VARCHAR(20),
    intent     VARCHAR(50),
    action     VARCHAR(50),
    status     VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_log (user_id)
);
```
---
10. Async & Scalability
10.1 Async Design Pattern
```python
# All I/O is non-blocking
@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    payload = await InputProcessor.process(request)
    
    # Parallel context fetch
    context = await build_context(payload)
    
    intent = route_intent(payload.text)
    decision = run_decision_engine(intent, context)
    context.decision = decision
    
    llm_output = await call_groq(context)
    action_result = await execute_action(llm_output.action, context)
    
    # Fire-and-forget logging (non-blocking)
    asyncio.create_task(log_learning(context, llm_output, action_result))
    asyncio.create_task(log_event(payload.user_id, intent.primary, llm_output.action, action_result.status))
    
    return ChatResponse(message=llm_output.message, action=llm_output.action)
```
10.2 Parallel Context Fetching
```python
async def build_context(payload: InputPayload) -> Context:
    user_data, rag_results, memory = await asyncio.gather(
        fetch_user_data(payload.user_id),
        query_pageindex(payload.text),
        fetch_memory(payload.user_id)
    )
    ...
```
10.3 Session Cache (In-Memory)
```python
from cachetools import TTLCache

session_cache = TTLCache(maxsize=500, ttl=1800)  # 30-min TTL

async def fetch_user_data(user_id: str) -> dict:
    if user_id in session_cache:
        return session_cache[user_id]
    data = await db.fetchone("SELECT * FROM users WHERE user_id = %s", (user_id,))
    session_cache[user_id] = data
    return data
```
10.4 Database Connection Pool
```python
import aiomysql

pool = await aiomysql.create_pool(
    host=settings.DB_HOST,
    user=settings.DB_USER,
    password=settings.DB_PASSWORD,
    db=settings.DB_NAME,
    minsize=3,
    maxsize=15,
    autocommit=True
)
```
---
11. Error Handling & Fallbacks
11.1 LLM Failure
```python
async def call_groq(context: Context) -> LLMOutput:
    try:
        response = await groq_client.chat.completions.create(...)
        return parse_llm_response(response)
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return LLMOutput(
            action=context.decision.priority_action,
            message="I'm having trouble right now. Please try again or contact support."
        )
```
11.2 DB Failure
```python
async def fetch_user_data(user_id: str) -> dict:
    try:
        return await db.fetchone("SELECT * FROM users WHERE user_id = %s", (user_id,))
    except Exception as e:
        logger.error(f"DB error for user {user_id}: {e}")
        return {}  # Return empty facts; Decision Engine handles missing data gracefully
```
11.3 Unknown Intent
```python
if intent.primary == "UNKNOWN":
    return Decision(
        allowed_actions=["provide_information"],
        priority_action="provide_information",
        reason="Unrecognized intent — conversational fallback"
    )
```
11.4 Safe Fallbacks Summary
Failure Point	Fallback Behavior
Groq timeout / error	Use `priority_action` with static fallback message
DB connection error	Return empty context; Decision Engine uses defaults
Unknown intent	Route to `provide_information` (conversational mode)
Invalid LLM JSON	Use `priority_action`; re-use safe template message
Action not in map	Log error; return `provide_information`
---
12. Logging (Simple & Minimal)
12.1 What to Log
```python
@dataclass
class LogEntry:
    user_id: str
    intent: str
    action: str
    status: str   # "success" | "failure"
    timestamp: str
```
12.2 Where to Log
After Decision Engine — log chosen intent + priority action:
```python
asyncio.create_task(log_event(
    user_id=payload.user_id,
    intent=intent.primary,
    action=decision.priority_action,
    status="decided"
))
```
After Action Executor — log outcome:
```python
asyncio.create_task(log_event(
    user_id=payload.user_id,
    intent=intent.primary,
    action=action_taken,
    status="success" if result.success else "failure"
))
```
Inside Learning System — store full record:
```python
asyncio.create_task(log_learning(context, llm_output, action_result))
```
12.3 Logging Function
```python
async def log_event(user_id: str, intent: str, action: str, status: str):
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO logs (user_id, intent, action, status) VALUES (%s, %s, %s, %s)",
                    (user_id, intent, action, status)
                )
    except Exception as e:
        pass  # Logging must never crash the main flow
```
12.4 Rules
All logging is background tasks (`asyncio.create_task`) — never `await` in main flow
Do NOT log full context JSON (too large)
Do NOT use heavy frameworks (no ELK, no Datadog for this scale)
Logs table is indexed on `user_id` for fast retrieval
---
13. Frontend Integration (Chatur UI)
> ⚠️ **DO NOT redesign the Chatur UI.** Only modify API calls and extend input handling.
13.1 Responsibility Split
Layer	Responsibility
Chatur UI	Chat rendering, input capture, file picker, voice recording
Backend `/chat`	Intent, decision, LLM, action execution, response
13.2 API Contract
Endpoint: `POST /chat`
Request:
```json
{
  "user_id": "USR123",
  "session_id": "sess_abc",
  "text": "I want to return my order",
  "attachments": [
    {
      "type": "image",
      "content": "<base64 string>"
    }
  ]
}
```
Response:
```json
{
  "message": "Your return request has been approved. You'll receive a pickup confirmation shortly.",
  "action": "approve_return",
  "session_id": "sess_abc"
}
```
13.3 Chatur Modifications Required
File upload — add multipart form handling:
```javascript
// In Chatur's send handler
const formData = new FormData();
formData.append("text", userInput);
formData.append("user_id", userId);
if (fileInput.files[0]) {
  formData.append("file", fileInput.files[0]);
}
fetch("/chat", { method: "POST", body: formData });
```
Voice input — Chatur handles speech-to-text; pass result as `text`:
```javascript
const recognition = new webkitSpeechRecognition();
recognition.onresult = (e) => {
  userInput = e.results[0][0].transcript;
  sendMessage(userInput);  // same send flow
};
```
No other UI changes needed. All intelligence lives in the backend.
13.4 Backend File Handling
```python
@app.post("/chat")
async def chat_endpoint(
    text: str = Form(...),
    user_id: str = Form(...),
    session_id: str = Form(...),
    file: UploadFile = File(None)
):
    attachments = []
    if file:
        content = await file.read()
        if file.content_type == "application/pdf":
            extracted = extract_pdf_text(content)
            attachments.append({"type": "pdf", "content": extracted})
        elif file.content_type.startswith("image/"):
            b64 = base64.b64encode(content).decode()
            attachments.append({"type": "image", "content": b64})
    ...
```
---
14. Final End-to-End Flow
```
┌─────────────────────────────────────────────────────────────────┐
│                          CHATUR UI                              │
│   Text / File Upload / Voice (speech-to-text)                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │  POST /chat
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  [1] INPUT LAYER                                                │
│  • Normalize text                                               │
│  • Parse PDF → extract text                                     │
│  • Image → base64 / OCR text                                    │
│  → Output: InputPayload                                         │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  [2] CONTEXT BUILDER         (async parallel)                   │
│  • MySQL: user + order data   →  facts                          │
│  • PageIndex RAG              →  knowledge                      │
│  • MySQL learning_logs        →  memory                         │
│  • Attachments                →  evidence                       │
│  → Output: Context (partial)                                    │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  [3] INTENT ROUTER           (rule-based, regex)                │
│  • Match text → primary_intent + sub_intent                     │
│  • Assign mode: deterministic | conversational                  │
│  → Output: IntentResult                                         │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  [4] DECISION ENGINE         (deterministic rules only)         │
│  • Apply business rules + memory signals                        │
│  • Produce allowed_actions + priority_action + reason           │
│  → Output: Decision → written into context.decision             │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  [5] LLM LAYER (Groq llama3-70b-8192)                          │
│  • Input: Full Context JSON                                     │
│  • Output: {"action": "...", "message": "..."}                  │
│  • Validates action ∈ allowed_actions                           │
│  → Output: LLMOutput                                            │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  [6] ACTION EXECUTOR                                            │
│  • Map action → function                                        │
│  • Idempotency check                                            │
│  • Execute DB update                                            │
│  → Output: ActionResult (success/failure)                       │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  [7] LEARNING SYSTEM         (background task, non-blocking)    │
│  • Write to learning_logs                                       │
│  • Write to logs                                                │
│  • No effect on response time                                   │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                        RESPONSE                                  │
│  {"message": "...", "action": "...", "session_id": "..."}       │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
                       CHATUR UI
                  (renders message in chat)
```
---
Appendix: Project Structure
```
backend/
├── main.py                  # FastAPI app + /chat endpoint
├── config.py                # Settings (DB, Groq API key, etc.)
├── models/
│   ├── context.py           # Context, Decision, LLMOutput dataclasses
│   ├── intent.py            # IntentResult dataclass
│   └── action.py            # ActionResult dataclass
├── pipeline/
│   ├── input_processor.py   # Input Layer
│   ├── context_builder.py   # Context Builder
│   ├── intent_router.py     # Intent Router
│   ├── decision_engine.py   # Decision Engine
│   ├── llm_layer.py         # Groq integration
│   ├── action_executor.py   # Action Executor
│   └── learning_system.py   # Learning System
├── db/
│   ├── connection.py        # aiomysql pool
│   └── queries.py           # Named async query functions
├── rag/
│   └── pageindex_client.py  # PageIndex RAG wrapper
├── utils/
│   ├── logger.py            # log_event function
│   └── file_parser.py       # PDF/image extraction
└── requirements.txt
```
---
End of Architecture Document — v1.0