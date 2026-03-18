# start

Primary command:
- Run: `docker compose up --build`
- Then open: `http://127.0.0.1:5000/dashboard`

If Docker Desktop / daemon is not running (error about docker API / npipe):
- On Windows, start Docker Desktop:
  - Press Win, search for “Docker Desktop”, and launch it, **or**
  - Run in PowerShell:
    - `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"` (adjust path if installed elsewhere)
- Wait until Docker shows “Running”, then re-run: `docker compose up --build`

If the containers still fail to start:
- Run: `docker compose logs --tail=150`
- Paste the logs into chat for troubleshooting.

This command will be available in chat with `/start`.
