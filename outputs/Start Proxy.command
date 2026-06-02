#!/bin/zsh
cd "$(dirname "$0")"
python3 proxy_manager.py start
python3 proxy_manager.py test https://api.ipify.org
echo
echo "Proxy is running. Close this window when you are done reading."
read -k 1 "?Press any key to close..."
