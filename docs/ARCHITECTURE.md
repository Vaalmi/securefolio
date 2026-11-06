# Architecture

How SecureFolio is put together, and the rules the code keeps. `docs/TECH_STACK.md` has the technical detail of each part.

## The processes

| Process | Language | Job |
| --- | --- | --- |
| Backend (`backend/`, with `agent/` inside it) | Python, FastAPI | Owns every record and rule. Serves the app's API and the MCP tools on 127.0.0.1, and runs the Ask agent. |
| Worker (`worker/`) | Python | Watches folders, parses, reads scans and hard pages, embeds, and writes cards. Started and watched by the backend. |
| Shell (`desktop/src-tauri/`) | Rust, Tauri 2 | The Mac app: the window, the menu bar, the global hotkey, and starting the backend. |
| MCP bridge (`desktop/mcp-bridge/`) | Rust | A stdio MCP server for Claude Desktop and Claude Code that forwards calls to the backend. |
| App (`app/`) | TypeScript, React | The UI, inside the shell's WebView. |
| Ollama (optional) | | Local models for Private and Offline answers, cards and figures. |

## Privacy rules

These are what SecureFolio promises, and the backend enforces them whatever the app, the agent or an MCP client asks for.

- Your files, the index, embeddings, OCR and memory stay on your Mac. A cloud model receives only what a request needs: the question, the recent turns and summary of its conversation, the memories that apply, the passages and document cards behind an answer, and on a retry the titles of near-miss documents. Nothing else leaves but: excerpts from folders where you turned on Cloud organizing, the small test request when you check a key, a page you ask a cloud model to read, and, if you run the evaluation, its non-Private answers for the judge.
- Private documents never reach a cloud model or MCP, in a question, a rewrite, a summary, a memory or a card.
  - Adding Private documents to a Standard conversation asks first: answer on the Mac, or leave them out.
  - A conversation that has touched Private documents stays Private for good.
- Whether a request may go to the cloud is decided before any model is called, by `backend/policy.py`.
- Pages are read on your Mac. A page goes to a cloud model only when you ask for that page, from a Standard document, with the cost shown; never from a Private document or in Offline mode.
- Offline mode makes no network connection off the Mac, from any part of the app. Connections within the Mac, such as to Ollama, are fine.
- SecureFolio never changes, moves or deletes your original files. Removing a document from the library never deletes a file on disk. Watched folders are read where they are; files you drag in are copied into the library, and deleting such a copy is a separate action, "Move copy to Trash".
- Every citation points to a passage or document card the answer was given. A citation that doesn't match one is dropped.
- Text inside your documents is data, never an instruction. It reaches a model only inside a `search_result` block or a `<source>` tag, never in the instructions. Nothing a model can call through SecureFolio changes anything.
- Answers and emails never load remote images or run HTML.
- The app listens only on 127.0.0.1 and refuses any request without the token for this launch. MCP has a token of its own that opens only the MCP tools, and every read through it is checked against the folder's current settings.
- Cloud keys live in the Keychain, never in a file.
- Background cloud work stops when the month's spend reaches your cap. A Deep run estimated at over $0.50 asks first.

Sensitivity travels with the data: every chunk, card, note, message, summary and memory carries the sensitivity of what it came from, and `policy.py` decides from that, never from the scope alone.

What enforces them, and where that stops:

- Offline mode is enforced inside SecureFolio's Python processes, by an audit hook that sees every socket, name lookup and subprocess (`backend/netguard.py`). Native code that opens its own sockets isn't seen by it; the packaged app has been run under a macOS sandbox that denies the network, to show nothing does. It isn't an OS-level firewall.
- Each process turns off LangSmith tracing and Hugging Face telemetry when it starts, whatever its environment says (`backend/telemetry.py`).
- The app window's Content Security Policy limits it to the app itself and the backend's port.
- The library, its index, conversations, managed copies and backups are stored unencrypted in the vault (`~/Library/Application Support/SecureFolio/`), readable by anything running as your user. Use FileVault.

The tests in files named `test_privacy_*.py` prove these rules. A stub cloud model records everything it receives, and the tests assert that no Private-derived text or image reaches it on any path. The Offline tests block every connection off the machine and fail on any attempt.

## Module boundaries

Each service is reached through one module, which holds its address and settings, and nothing else reaches it. `agent/tests/test_boundaries.py` and an ESLint rule check the import rules.

- The app reaches the backend only through `app/src/api.ts`, on `app/src/connection.ts`.
- The agent reaches the backend's service modules (the database, policy, the index, spend) only through `agent/api.py`, in-process, and never the API's routes or gates.
- The backend reaches the agent only through `backend/agent_service.py`.
- Chat models are built only in `agent/models.py`, and every cloud model call goes through it: it asks `policy.py` first and writes the egress log.
- LanceDB is reached only through `backend/index.py`, embeddings through `backend/embedder.py`, and reranking through `backend/reranker.py`.
- Your original files are opened only by `backend/originals.py`, read-only.
- Each cloud key is read and written only by `backend/keychain.py`, and each provider's name, host, Keychain account and ID header is written only in `backend/providers.py`.
- Who answers a question, and who runs its helpers, is decided only by `policy.route()`.
- A model's capabilities and price come only from `backend/model_catalog.py`.
- OCR runs only through `worker/ocr.py`, and only `worker/unlock.py` handles a document's password, in memory.
- Each macOS-only call (dataless flags, the sleep assertion, LaunchServices) goes through one module, with a stand-in for Linux and the tests.
- The MCP tools live only in `backend/mcp_server.py`, and the bridge reaches the backend only through `runtime.json` and the MCP routes.
- The worker uses the same service modules as the backend and the agent's model gateway, never the API's routes or gates.

Inside the agent:

- `harness.py` builds and runs the Ask graph: its nodes, edges and state. The graph is fixed: the order of its steps, the policy check, verifying citations, saving the turn and extracting memories are code, never a model's choice.
- `models.py` builds models, reads their usage and gates every cloud call.
- `checkpoints.py` keeps the graph's progress, restores the pre-turn checkpoint on Stop, and prunes idle threads.
- `memory.py` owns the `memories` table: scoring, extraction, editing, and deletion with its tombstones.
- A run's ids (the thread, the run, the scope and the sensitivity) come from the run's context, never from a model. A model's output never chooses which documents a run may read.

## The API

Every operation has an `operation_id` and a one-line docstring, and `api_rules.py` checks this against the OpenAPI schema. The MCP tools take no optional inputs, because an optional field invites a model to guess; variants are discriminated unions instead. Every refusal is a status with a one-line reason that says what is wrong.
