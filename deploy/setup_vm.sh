#!/usr/bin/env bash
# Run this on a fresh Ubuntu VM (e.g. Google Cloud Free Tier e2-micro) to
# set up the TradeCopilot scanner as an always-on systemd service.
#
# Usage: ssh into the VM, then:
#   git clone https://github.com/sumitpandit1092/tradecopilot-v2.git
#   cd tradecopilot-v2
#   bash deploy/setup_vm.sh
#
# Afterwards, create ~/tradecopilot-v2/.env (see .env.example) before
# starting the service.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
pip install --upgrade --no-cache-dir git+https://github.com/rongardF/tvdatafeed.git

deactivate

# The service file is a template with ubuntu/home/ubuntu placeholders --
# substitute the actual login user and repo path (GCP's default user
# varies by how the VM/SSH key was created, so it's not always "ubuntu").
sed \
    -e "s#/home/ubuntu/tradecopilot-v2#${REPO_DIR}#g" \
    -e "s/User=ubuntu/User=${USER}/" \
    deploy/tradecopilot-scanner.service | sudo tee /etc/systemd/system/tradecopilot-scanner.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable tradecopilot-scanner

echo ""
echo "Setup complete. Next steps:"
echo "  1. Create $REPO_DIR/.env (copy from .env.example and fill in TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)."
echo "  2. Start the service:  sudo systemctl start tradecopilot-scanner"
echo "  3. Check it's running: sudo systemctl status tradecopilot-scanner"
echo "  4. Tail live logs:     journalctl -u tradecopilot-scanner -f"
