#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""让 `python -m l10n <子命令>` 可用。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
