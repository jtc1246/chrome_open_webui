#!/bin/bash
# 一键构建: origin/ChromePublic.apk -> out/ChromePublic_patched.apk

set -e
set -o pipefail
cd "$(readlink -f "$(dirname "$0")")"

python3 -u build.py origin/ChromePublic.apk out/ChromePublic_patched.apk
