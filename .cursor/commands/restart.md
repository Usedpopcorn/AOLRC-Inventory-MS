# restart

Restart Docker Compose services only if they are currently running.

Run:

$running = docker compose ps --status running -q
if ($running) { docker compose restart } else { Write-Output "No running containers to restart." }

This command will be available in chat with /restart
