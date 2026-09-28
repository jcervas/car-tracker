#!/bin/zsh
# Double-click to start the car tracker and open it in your browser.
cd "$(dirname "$0")"
(sleep 1 && open "http://localhost:8777") &
python3 server.py
