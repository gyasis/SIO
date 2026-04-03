---
name: sio-report
description: Generate a visual HTML report of SIO analysis. Ask naturally like "give me a report" or "show me a summary of SIO findings".
---

# SIO Report — Visual HTML Summary

Run this when the user asks for a summary or report of SIO analysis. Generates an HTML report with charts and tables showing errors, patterns, suggestions, and rule effectiveness.

## Triggers (natural language)

- "Give me a report"
- "Show me a summary of SIO findings"
- "Generate an SIO report"
- "I want to see the full picture"
- "Create a visual report"
- "Export SIO analysis"

## Execution

Generate and open the HTML report:

```bash
#!/bin/bash
set -e
sio report --html --open
```

## What the Report Contains

| Section | What It Shows |
|---|---|
| **Error breakdown** | Errors by type, tool, and session — with trend lines |
| **Pattern clusters** | Grouped errors with frequency and severity |
| **Suggestions** | Pending, approved, and applied suggestions with confidence scores |
| **Rule velocity** | Applied rules and whether they are reducing errors |
| **Budget usage** | Current line/token usage of instruction files |

## After Generation

Tell the user:
- The report has been saved as an HTML file and opened in their browser
- The report is a point-in-time snapshot — run again after new sessions for updated data
- If the browser did not open, provide the file path so they can open it manually

## Follow-up Actions

Suggest next steps based on what the report shows:
- Many unaddressed patterns? -> "Want to generate suggestions?" -> run `/sio-suggest`
- Rules not working? -> "Want to check rule velocity?" -> run `/sio-velocity`
- Want to drill into specific errors? -> "Let me scan for details" -> run `/sio-scan`
