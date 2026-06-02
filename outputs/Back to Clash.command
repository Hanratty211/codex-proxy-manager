#!/bin/zsh
cd "$(dirname "$0")"
python3 proxy_manager.py proxy-clash
echo
echo "System proxy switched back to Clash 127.0.0.1:7890."
read -k 1 "?Press any key to close..."
