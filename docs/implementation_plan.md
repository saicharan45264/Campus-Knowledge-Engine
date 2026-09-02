# Campus Knowledge Engine - V2 Architecture Upgrade Plan

This document outlines the implementation steps to upgrade the Campus Knowledge Engine to a production-ready V2 architecture, incorporating Celery for background jobs, LlamaIndex for LLM orchestration, Next.js for the frontend, and Supabase for authentication.

## User Review Required
> [!IMPORTANT]
> Since we are migrating the frontend to **Next.js**, the existing Vanilla JS/HTML files will be deprecated. The new frontend will likely live in a separate `/frontend-next` directory during development.
> We will need to set up a **Supabase** account to get API keys for authentication.
> **Redis** will need to be added to the `docker-compose.yml` to support Celery.

## Open Questions
> [!WARNING]
> 1. Do you want to keep the new Next.js frontend in a completely separate repository, or keep it inside this monorepo (e.g., in a `frontend-v2/` folder)?
> 2. Should we host the Redis instance locally via Docker Compose, or use a managed cloud Redis provider?
> 3. For LLM Orchestration, `LlamaIndex` is specifically optimized for RAG (Retrieval-Augmented Generation) and is highly recommended over LangChain for this use case. Are you okay with proceeding with LlamaIndex?

---

## Proposed Changes

### 1. Robust Background Processing (Celery + Redis)
**Goal:** Prevent heavy PDF vision extraction from blocking the FastAPI server and handle crash recovery.

- **[MODIFY]** `docker-compose.yml`: Add a `redis` container.
- **[NEW]** `backend/celery_worker.py`: Initialize the Celery application and configure it to use Redis as the broker.
- **[MODIFY]** `backend/app.py`: Replace FastAPI `BackgroundTasks` with Celery `.delay()` calls for `process_syllabus_background` and `process_pyq_background`.
- **[MODIFY]** `backend/requirements.txt`: Add `celery` and `redis`.

---

### 2. LLM Orchestration (LlamaIndex)
**Goal:** Standardize document chunking, prompt formatting, and interaction with Ollama.

- **[MODIFY]** `backend/requirements.txt`: Add `llama-index` and `llama-index-llms-ollama`.
- **[MODIFY]** `backend/utils.py`: 
  - Refactor `generate_answer` and `generate_answer_stream` to use LlamaIndex's `Ollama` LLM class.
  - Refactor the manual prompt formatting to use LlamaIndex's `PromptTemplate`.
- **[MODIFY]** `backend/app.py`: Refactor the RRF (Reciprocal Rank Fusion) and Neo4j context builder to feed directly into a LlamaIndex query engine.

---

### 3. Frontend Migration (Next.js & React) - *Optional*
**Goal:** Move from Vanilla JS to a modern state-managed framework for robust chat streaming and UI stability, **without changing a single pixel of the current design.**

*Note: Your hard work on the UI will NOT be lost! We will strictly port your existing HTML and CSS over to React components so that the app looks 100% identical to how it looks today. The canvas animations and custom layouts will remain exactly the same—only the underlying code managing the state will change. (If you prefer to keep Vanilla JS, we can just skip this step entirely!)*

- **[NEW]** `/frontend-v2`: Initialize a new Next.js project (`npx create-next-app`).
- **[NEW]** `/frontend-v2/components/ChatLayout.tsx`: Replicate the exact existing student chat HTML and CSS.
- **[NEW]** `/frontend-v2/components/GraphAnimation.tsx`: Port the exact Canvas-based Knowledge Graph animation to a React `useEffect` hook.
- **[NEW]** `/frontend-v2/lib/api.ts`: Centralize all fetch calls to the FastAPI backend.
- **[DELETE]** `/frontend` (To be done *only* after V2 is fully verified and looks identical).

---

### 4. Authentication (Supabase Auth)
**Goal:** Replace hardcoded JWTs with enterprise-grade, secure authentication.

- **[MODIFY]** `backend/requirements.txt`: Add `supabase` python SDK.
- **[MODIFY]** `backend/app.py`: 
  - Remove the hardcoded `_USERS` dictionary and the custom `_create_token` function.
  - Update the `/login` route to verify tokens using Supabase Auth, or offload login entirely to the Next.js frontend and simply verify JWTs in FastAPI using Supabase's public keys.
- **[NEW]** `/frontend-v2/app/login/page.tsx`: Implement the Supabase Auth UI (supporting email/password and potentially Google/GitHub).

---

## Verification Plan

### Automated Tests
- N/A - The system relies heavily on integration. We will rely on manual staging verification.

### Manual Verification
1. **Docker Startup:** Run `docker compose up -d` and verify Postgres, Neo4j, and the new Redis container start cleanly.
2. **Celery Worker:** Start the Celery worker and upload a large PDF to ensure tasks execute in the background without freezing the FastAPI terminal.
3. **LlamaIndex Generation:** Ask a complex multi-hop question in the chat and verify the streamed response matches or exceeds the quality of the previous manual implementation.
4. **Next.js UI:** Verify the Canvas animation loads smoothly in React and the chat streams text properly.
5. **Authentication:** Log out, attempt to access protected API endpoints (should get 401 Unauthorized), and log back in using Supabase credentials.
