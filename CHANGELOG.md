# Changelog

## 2.0.0

The first public release, as source code under the Apache License 2.0. macOS on Apple Silicon only, built from source.

SecureFolio was called DocSynth before this release. If you ran DocSynth:

1. Quit DocSynth, and turn off its Open at login in its Settings.
2. Open SecureFolio. On its first start it moves DocSynth's library to its own folder (`~/Library/Application Support/SecureFolio`), with your notes, chats, cards and copied files, and copies your cloud keys to its own Keychain entries. It won't start while DocSynth is still running.
3. Remove `DocSynth.app`. In Claude Desktop, remove the DocSynth extension and install SecureFolio's from Settings, Privacy and MCP. For Claude Code, run `claude mcp remove docsynth`, then add SecureFolio's command from the same place.

DocSynth's old Keychain entries stay until you delete them in Keychain Access. Links to passages from before the rename (`docsynth://`) still open in SecureFolio.

- Watched folders with Standard and Private sensitivity, drag and drop, and an import tray.
- PDF, Office, email, web page, text and image formats, with scans, tables, charts and bad text layers read on the Mac.
- Search as you type, a global hotkey, and a command palette.
- Answers with citations that open the exact place, from Anthropic, OpenAI, OpenRouter or a local model through Ollama, with Offline mode.
- Cards, collections, notes, threads, memory, backups, and an import from DocSynth v1.
- A read-only MCP server for Claude Desktop and Claude Code.
