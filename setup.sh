#!/usr/bin/env bash
# Setup script for the AD&D 2e PDF Compiler
set -e

echo "==> Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y tesseract-ocr poppler-utils libgl1

echo "==> Installing Python packages..."
pip3 install -r requirements.txt

echo ""
echo "Done! Set your Anthropic API key before running:"
echo "  export ANTHROPIC_API_KEY=sk-..."
echo ""
echo "Then run:"
echo "  python3 compiler.py your_book.pdf -o output.md"
