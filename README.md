# chatur_bot

## Local integration quick test

1. Set backend env in `.env` and start API:
   `uvicorn main:app --reload --host 0.0.0.0 --port 8000`
2. Serve frontend from `frontend/` on `http://localhost:3000` (or update `nm_api_base` in localStorage).
3. Run these chat checks in UI:
   - `Where is my order?`
   - `My product is broken`
   - `What is refund policy?`
