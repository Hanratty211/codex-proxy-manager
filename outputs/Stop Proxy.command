#!/bin/zsh
cd "$(dirname "$0")"
python3 proxy_manager.py stop
echo
echo "Proxy stopped and Wi-Fi system proxy disabled."
read -k 1 "?Press any key to close..."
