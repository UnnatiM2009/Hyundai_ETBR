#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "Installing/checking dependencies..."
python3 -m pip install -r requirements.txt --quiet
echo
echo "Starting Hyundai Dealership Dashboard..."
python3 run.py
