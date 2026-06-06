#!/bin/bash
# Package CurriculumLens for AirDrop
# This script creates a zip file of the project, excluding heavy dependencies like node_modules and venv
# so that the zip file is small and fast to share.

echo "📦 Packaging CurriculumLens for AirDrop..."

zip -r CurriculumLens_Share.zip . \
    -x "*/node_modules/*" \
    -x "*/venv/*" \
    -x "*/__pycache__/*" \
    -x "*/.git/*" \
    -x "*/.next/*" \
    -x "*/data/uploads/*" \
    -x "*/.DS_Store" \
    -x "package_for_airdrop.sh"

echo "✅ Done! You can now AirDrop 'CurriculumLens_Share.zip' to your teammates."
