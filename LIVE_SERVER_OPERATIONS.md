# Live Server Operations (Windows)

This guide is only for the **live server flow** on this machine:

- Waitress app server (`127.0.0.1:8000`)
- Cloudflare tunnel (`www.aolrcinventory.org`)

Use this when you need to **start**, **stop**, **restart**, or recover when there are no visible terminals.

---

## 1) Normal Start (Recommended)

Open two PowerShell terminals in repo root:

### Terminal A - App server

```powershell
.\start_server.bat
```

Expected output includes:
- "Starting AOLRC live-test app with Waitress."
- local listen host/port

### Terminal B - Tunnel

```powershell
.\start_tunnel.bat
```

Expected output includes:
- "Starting named Cloudflare Tunnel"
- tunnel run logs

---

## 2) Normal Stop (When Terminals Are Open)

In each running terminal:

1. Click terminal
2. Press `Ctrl + C`
3. Wait for process exit

Do this for:
- Waitress terminal
- Cloudflare tunnel terminal

You can also use script-based stop commands:

```powershell
.\stop_server.bat
.\stop_tunnel.bat
```

---

## 3) Check If Live Server Is Running

Run these checks from PowerShell:

### Is Waitress listening on 8000?

```powershell
Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 8000 -ErrorAction SilentlyContinue
```

If it returns a row, the app is listening.

### Is cloudflared process running?

```powershell
Get-Process cloudflared -ErrorAction SilentlyContinue
```

If it returns a process, tunnel process exists.

---

## 4) Restart (Clean Standard Procedure)

Use this if you just want a clean restart:

1. Run:
   - `.\restart_live_stack.bat`
2. Verify:
   - local: `http://127.0.0.1:8000/login`
   - public: `https://www.aolrcinventory.org/login`

---

## 5) No Visible Terminals, But App Seems Running

Use this exact workflow when terminal windows were closed/lost.

Fast path:

```powershell
.\stop_live_stack.bat
.\restart_live_stack.bat
```

Manual path (if needed):

## A) Identify process using port 8000

```powershell
$conn = Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 8000 -ErrorAction SilentlyContinue
if ($conn) { Get-Process -Id $conn.OwningProcess }
```

If process name is `waitress-serve` (or python hosting waitress), that is your app server.

## B) Stop the process on port 8000

```powershell
$conn = Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 8000 -ErrorAction SilentlyContinue
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force }
```

## C) Stop all cloudflared processes

```powershell
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
```

## D) Confirm both are stopped

```powershell
Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 8000 -ErrorAction SilentlyContinue
Get-Process cloudflared -ErrorAction SilentlyContinue
```

Both commands should return no running targets.

## E) Start fresh

```powershell
.\start_server.bat
.\start_tunnel.bat
```

---

## 6) Emergency "Hard Reset" for Live Processes

Use only when things are stuck and normal restart fails.

```powershell
# Stop listener on 8000 (if any)
$conn = Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 8000 -ErrorAction SilentlyContinue
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force }

# Stop cloudflared
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
```

Then restart normally:

```powershell
.\restart_live_stack.bat
```

---

## 7) If Start Fails

## Problem: `start_server.bat` fails immediately

Check:

1. Virtualenv exists:
   - `.venv\Scripts\waitress-serve.exe`
2. If missing:
   - `.\scripts\bootstrap_dev.ps1`
3. Retry:
   - `.\start_server.bat`

## Problem: `start_tunnel.bat` fails immediately

Check:

1. `cloudflared` installed:
   - `Get-Command cloudflared -ErrorAction SilentlyContinue`
2. If missing:
   - `winget install --id Cloudflare.cloudflared`
3. If auth/tunnel errors:
   - `cloudflared tunnel login`
   - verify tunnel name exists (`aolrc-inventory-live-test`)

## Problem: Port 8000 already in use

1. Find owner:
   - `Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 8000`
2. Stop owner process:
   - `Stop-Process -Id <PID> -Force`
3. Start server again.

---

## 8) Quick "Status + Restart" Script Block

Preferred command:

```powershell
.\restart_live_stack.bat
```

Copy/paste manual block if scripts are unavailable:

```powershell
# Stop app listener if present
$conn = Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 -LocalPort 8000 -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host "Stopping PID $($conn.OwningProcess) on 127.0.0.1:8000"
    Stop-Process -Id $conn.OwningProcess -Force
}
else {
    Write-Host "No listener found on 127.0.0.1:8000"
}

# Stop cloudflared if present
$cf = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($cf) {
    Write-Host "Stopping cloudflared process(es)"
    $cf | Stop-Process -Force
}
else {
    Write-Host "No cloudflared process found"
}

# Start app + tunnel
Start-Process powershell -ArgumentList '-NoExit','-Command','cd "c:\CODE PROJECTS\LIVE REAL TEST AOLRC INVENTORY"; .\start_server.bat'
Start-Process powershell -ArgumentList '-NoExit','-Command','cd "c:\CODE PROJECTS\LIVE REAL TEST AOLRC INVENTORY"; .\start_tunnel.bat'
```

---

## 9) Post-Restart Verification Checklist

After every restart:

1. Local login page responds:
   - `http://127.0.0.1:8000/login`
2. Public login page responds:
   - `https://www.aolrcinventory.org/login`
3. Login works for a known account.
4. If testing email paths, confirm password reset and login verification email still send.
5. If code changed DB models/migrations, run:
   - `.\upgrade_live_postgres_db.bat`

---

## 10) Safety Notes

- Do not expose PostgreSQL publicly.
- Do not run Flask dev server for live test.
- Do not commit secrets/tokens/passwords.
- Always stop and restart both components (server + tunnel) together when troubleshooting.
- Use `.\stop_live_stack.bat` and `.\restart_live_stack.bat` as the primary recovery commands.
