Slide Title: Problem Statement

* E-commerce support often feels slow: users wait for status, return, and refund updates.
* Decision quality is inconsistent when agents interpret policies differently.
* Personalization is weak when user/order context is not assembled in one place.
* Policy handling is error-prone without deterministic rule enforcement before response generation.

Slide Title: Solution Overview

* Built an AI-powered FastAPI support backend with a deterministic-first pipeline.
* Business decisions are made by `decision_engine`; LLM is restricted to response generation.
* Current MVP handles order tracking, complaints, return/refund, greetings, and clarification fallback.
* Supports text-first chat plus voice/file entrypoints with compatible frontend API contracts.

Slide Title: System Architecture

* User request enters `/chat` and is normalized into a unified payload.
* `context_builder` composes task, facts, policy rules, lightweight knowledge, and memory.
* `intent_router` classifies intent + emotion + confidence (rule-based keywords).
* `decision_engine` returns deterministic `allowed_actions`, `priority_action`, and reason.
* `llm_service` generates structured JSON (`action`, `message`) under strict constraints.
* `action_executor` applies idempotent action handlers, then learning/logging capture outcomes.

Slide Title: Key Innovation

* Deterministic AI guardrails: LLM cannot override policy decisions.
* Action safety: post-LLM action validation enforces `allowed_actions` / `priority_action`.
* Context filtering keeps prompts small and factual (status, product, policy limits).
* Continuous learning loop computes `preferred_action` from interaction history.

Slide Title: Tech Stack

* FastAPI backend with async handlers and SSE stream support.
* Groq LLM (`llama3-70b-8192`) with low-temperature structured JSON output.
* LangChain-style control in LLM layer (prompt + output parsing constraints).
* MySQL + policy config path prepared (`MYSQL_*` settings), with mock/in-memory MVP execution.
* PageIndex-style RAG source path (`RAG_DATA_PATH`) with file-backed policy knowledge loading.
* Chatur UI frontend integrated via `/chat`, `/chat/stream`, `/voice/chat`, `/upload`, `/history`.

Slide Title: Features

* Rule-based intent detection: order tracking, complaint, return/refund, product query, greeting.
* Deterministic policy decisions for return window and replacement/refund eligibility.
* Action automation: replacement/refund initiation, status/ETA support, clarification handling.
* Idempotency protection prevents duplicate processing for repeated same-intent actions.
* Minimal learning memory stores intent/action/outcome and derives preferred action.
* Voice + file aware flow includes mock extraction signals for image/PDF uploads.

Slide Title: Demo Flow

* 1. User asks a support query in chat (or voice/file-assisted input).
* 2. System builds filtered context and classifies intent/emotion/confidence.
* 3. Decision engine selects allowed actions + priority action deterministically.
* 4. LLM produces concise customer message and constrained action JSON.
* 5. Executor validates action, performs operation, and returns execution result.
* 6. Learning/logging stores outcome and updates behavior signal (`preferred_action`).

Slide Title: Results / Impact

* Faster support loop through API-first automation and immediate action suggestions.
* Consistent outcomes from deterministic rules instead of free-form agent interpretation.
* Lower human support burden for routine tracking/return/refund scenarios.
* More personalized interactions using intent history and preferred-action behavior signals.

Slide Title: Future Scope

* Real-time support analytics dashboard (intent trends, action outcomes, SLA view).
* Deeper personalization using richer user/order history and long-term memory.
* Seller-side integration for direct fulfillment, replacement, and refund workflow sync.
* Fraud/abuse detection layer using complaint/refund patterns and anomaly signals.
