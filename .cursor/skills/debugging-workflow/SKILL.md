---
name: debugging-workflow
description: Follows a consistent debugging workflow: locate error (file + line), confirm Docker vs local, request logs if Docker, propose minimal fix with exact path and snippet, then give verification steps. Use when debugging errors, interpreting tracebacks, or when the user reports a crash or exception.
---

# Debugging Workflow

When helping debug an error, follow these steps in order.

## 1. Identify where the error occurs

- From the traceback, determine **file path** and **line number** of the failing line (and the call chain if relevant).
- State them explicitly, e.g. `app/routes/inventory.py line 42`.

## 2. Confirm runtime environment

- Ask or infer: **Docker** (`docker compose up`) or **local** (venv / system Python).
- If unclear, ask: "Are you running the app in Docker or locally?"

## 3. Request logs (if Docker)

- If the user is running in Docker, ask for:
  ```bash
  docker compose logs --tail=150
  ```
- Use this output (plus any traceback they shared) to diagnose; do not suggest manual schema changes or non-migration DB edits.

## 4. Propose a minimal fix

- Give **exact file path** (project-relative, e.g. `app/models/item.py`).
- Provide a **short, concrete snippet** (the minimal change), not a long rewrite.
- Prefer the smallest change that fixes the issue.

## 5. Verification steps

- Provide a **command** the user can run to verify the fix.
- State the **expected output** (or that the error should no longer appear).

**Example verification:**
```bash
docker compose up --build
# Expected: app starts without traceback; no error in logs for the previously failing request.
```

---

## Summary checklist

When responding to a debug request, ensure:

- [ ] Error location stated as file + line
- [ ] Docker vs local confirmed
- [ ] If Docker: user asked for `docker compose logs --tail=150` (or logs already provided)
- [ ] Fix specified with exact path + minimal snippet
- [ ] Verification: command + expected outcome
