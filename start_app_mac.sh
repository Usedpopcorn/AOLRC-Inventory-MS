#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "=========================================="
echo " AOLRC Inventory - Start App (Docker)"
echo "=========================================="
echo
echo "App will be available at:"
echo "  http://127.0.0.1:5000/dashboard"
echo

docker compose up --build

#After creating it on mac, run: chmod +x start_app_mac.sh