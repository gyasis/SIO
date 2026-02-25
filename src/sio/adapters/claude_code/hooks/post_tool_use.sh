#!/usr/bin/env bash
# PostToolUse hook shell wrapper for Claude Code
# Reads JSON from stdin, passes to Python handler, writes result to stdout
exec python3 -m sio.adapters.claude_code.hooks.post_tool_use
