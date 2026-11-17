# Changelog

## 2.1.0

The same answer from any model: every model gets the same rules, and every answer's figures are checked against its sources.

- Each figure in an answer, such as an amount, a date or a percentage, is checked against the passages its sentence cites. One they don't show is marked "Not in its sources", and one SecureFolio added up itself is tagged "Worked out". Nothing is rewritten.
- A citation opens at the figures its sentence used, wherever they can be placed for certain.
- With no model to answer, Ask still finds the passages for your question, on your Mac, labelled as not an answer.
- "This was wrong" under an answer saves it as a question in your own set, on your Mac, which the evaluation asks again.
- Settings shows a measured model's scores where you choose it, and a local model's as measured on your Mac.
- Every model, cloud or local, gets the same answering rules. Sheet rows keep their column names, citations written as `[1, 2]` are read, and a sentence keeps its citation when the next one cites the same passage.
- A local model's helper calls have limits on their length and time, and SecureFolio sets the model's context instead of leaving it to Ollama.
- The evaluation measures any model, local ones included, keeps every run with its times, and with `--limit-usd` stops before a request that could pass the limit.
- Dependency updates, including pdf.js 6, which fixes a security issue in opening a malicious PDF.

## 2.0.0

The first public release, as source code under the Apache License 2.0. macOS on Apple Silicon only, built from source.

- Watched folders with Standard and Private sensitivity, drag and drop, and an import tray.
- PDF, Office, email, web page, text and image formats, with scans, tables, charts and bad text layers read on the Mac.
- Search as you type, a global hotkey, and a command palette.
- Answers with citations that open the exact place, from Anthropic, OpenAI, OpenRouter or a local model through Ollama, with Offline mode.
- Cards, collections, notes, threads, memory and backups.
- A read-only MCP server for Claude Desktop and Claude Code.
