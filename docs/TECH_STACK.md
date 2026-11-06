# Tech stack

The technical choices behind SecureFolio, and why. `docs/ARCHITECTURE.md` covers the module boundaries and the privacy rules the code enforces.

## Where things run

SecureFolio is a Mac app for one person, and everything runs on that Mac. It has three processes, plus Ollama when a local model is used:

- **The backend:** one Python process. It owns every record and rule, serves the app's API and the MCP tools on 127.0.0.1, and runs the Ask agent.
- **The worker:** one Python process for ingestion and organizing, started and watched by the backend. Parsing and OCR are heavy, and a crash there must not take the API down.
- **The desktop shell:** Tauri. It bundles the UI and the Python runtime, starts the backend, and stays in the menu bar.

The backend and the worker ship together, in one bundle, and share code. The worker uses the backend's service modules (the database, originals, the index, the embedder, policy, spend) and the agent's model gateway, each through its own module. It never uses the API's routes or gates.

The backend writes `runtime.json` (`0600`), holding its port, its pid, a version and the two tokens, and removes it when it stops.

## The parts

- **`backend/`:** FastAPI with SQLAlchemy Core on SQLite at `library.db` in the vault (`~/Library/Application Support/SecureFolio/` by default, `SECUREFOLIO_VAULT` in development). SQLite runs in WAL mode, with `foreign_keys=ON` and a 5 s busy timeout.
  - **One database holds everything:**
    - sources (watched folders, their sensitivity, auto-organize and MCP switches), documents, `document_locations`, revisions, elements, and chunks with their embeddings as float16;
    - `documents_fts` (FTS5), for titles and metadata;
    - cards, collections, notes, threads and messages;
    - `memories`, with tombstones;
    - jobs, `message_batches` and the egress log;
    - the LangGraph checkpoints.
  - **Managed copies:** files dragged in, including saved web pages, are copied content-addressed into the vault's `objects/`. "Move copy to Trash" applies only to these copies.
  - **Rules the backend owns:**
    - Every record moves through its states by conditional updates, so only one caller can make a given move.
    - Every refusal answers `{"detail": "..."}` with one line of text.
    - Schema changes ship as Alembic migrations in `backend/migrations/`, run at startup after a backup.
  - **Gates:**
    - `auth.py` issues the two per-launch tokens and compares them with `secrets.compare_digest`.
    - `dependencies.py` holds the gates:
      - `AppToken` needs the token Tauri injects into the WebView.
      - `McpToken` needs the MCP token and opens only the MCP routes.
      - Neither stands in for the other.
    - Every request's `Host` must be `127.0.0.1:<port>` or `localhost:<port>`.
    - A request that carries an `Origin` must come from `tauri://localhost`. In development and the tests, `http://localhost:<vite port>` and `http://127.0.0.1:<vite port>` are also accepted. Release builds accept neither, and have no debug routes.
  - **Privacy:** `policy.py` decides, before any model call, whether a request may go to the cloud.
    - It reads the sensitivity of everything in the payload: the passages, the thread's history and summary, memories, cards and page images.
    - A thread that has touched Private content is marked `private_tainted` for good.
    - Default scopes exclude Private sources.
  - **Originals:** `originals.py` is the only code that opens a user's file, and it opens it read-only. Nothing in SecureFolio writes, moves or deletes an original. Relocating a missing file records a new location after checking its content hash.
  - **Serving files:** `files.py` serves a file to the viewer with `Content-Security-Policy: default-src 'none'; sandbox` and `X-Content-Type-Options: nosniff`. HTML, SVG and XML are served as attachments.
  - **Retrieval:**
    - `index.py` is the one way to LanceDB: BM25 plus vector search fused by reciprocal rank, with prefilters on source, sensitivity, type, date, collection and tag.
    - Vectors are written to LanceDB only when a revision is published. A sweep at startup reconciles LanceDB with SQLite's active revisions, and `optimize()` runs when the queue drains. LanceDB can be rebuilt from the stored embeddings without re-parsing or re-embedding.
    - `embedder.py` runs Qwen3-Embedding-0.6B in-process with sentence-transformers on MPS. Each index generation records the model, its revision, its query and document instructions, the dimension, the metric and the truncation, and switching models builds a new generation beside the old one.
    - `reranker.py` runs Qwen3-Reranker-0.6B in-process, for Ask only, on the top 24. Typeahead search never reranks.
    - The model caches live in the vault's `models/`: `HF_HOME` and Docling's `artifacts_path` point there, and `HF_HUB_OFFLINE=1` is set once the first-run download is done.
  - **MCP:** `mcp_server.py` serves `search_library`, `read_passage`, `get_document` and `list_collections` over Streamable HTTP on loopback, with `mcp` 2.x.
    - The tools are read-only.
    - Every document, passage, neighbor and collection read is authorized against the folder's current settings.
    - Private sources never appear.
  - **The keys:** `keychain.py` is the only code that reads or writes a cloud key, one Keychain account per provider. Only the last four characters are ever shown.
  - **Spend:** `spend.py` keeps the month's spend from the egress log, with the dated price table. It stops background cloud work at the cap, and prices a Deep run, or a page the user asks a cloud model to read, before it starts.
  - **Backup:** `backup.py` runs nightly. It copies `library.db` through SQLite's backup API, the managed copies in `objects/`, and a manifest of the watched files' locations. LanceDB and thumbnails aren't backed up: they rebuild.
  - **Logs:** local structured logs with no document text at INFO, and a timeline for each job.
- **`worker/`:** the ingestion pipeline, run as a SQLite job queue.
  - Jobs are claimed with `BEGIN IMMEDIATE`, and every stage is idempotent.
  - **Cancel:** a job checks for cancellation between stages, and a cancelled job leaves no orphan rows or vectors.
  - **Pacing:** it runs at utility priority, only on power or above 20% battery, and holds a sleep assertion only while jobs are queued on power. The search, OCR and page-reading models of the backend and the worker together stay under 6 GB, and idle models unload after 5 minutes. The page-reading model loads only in the lower-priority hard-page job: it unloads the layout models and the worker's embedder before it loads, and unloads before the new revision is embedded. `backend/model_budget.py` coordinates the two processes.
  - **Discover:**
    - `watchfiles` watches the sources with a 3 s debounce, and a reconcile scan (inode, size, mtime) runs at start, hourly and after wake. The scan is the truth; events are hints.
    - Dataless iCloud and Dropbox files (`stat.SF_DATALESS`, `0x40000000`) are listed but not read.
  - **Fingerprint:** `locations.py` hashes the content and records each location.
    - A path is a move only when the old path is gone; otherwise it is another location of the same document.
    - The strictest sensitivity of a document's locations wins, and LanceDB's sensitivity column is updated at once.
  - **Parse:**
    - Docling 2.x, with the `ocrmac` OCR engine (Apple Vision) through `ocr.py`, for pages without a good text layer, including mixed pages.
    - A dedicated path for spreadsheets (`openpyxl`, `csv`), with python-pptx and pandas kept for Docling.
    - Born-digital pages that are single-column, with no ruling lines or images, skip the layout models (`born_digital.py`).
    - **Hard pages:** `hard_pages.py` is the only code that decides which pages are hard and runs their second reading, including a page the user asks a cloud model to read. It runs as a lower-priority job after the document is published, so a document is searchable before its hard pages are read, and it publishes a new revision only when a reading changed something. A hard page is read again from its image with Docling's VLM pipeline and Granite-Docling-258M on MLX, whose DocTags give each element a box. A page is hard when any of these holds; a score Docling leaves unset is ignored:
      - its page confidence has `mean_grade` FAIR or POOR, or `low_grade` POOR;
      - its text layer is broken (`replace_broken_text_layer`);
      - on a PDF page with a native text layer, leaving out the boxes of its pictures and charts: the text layer differs from a fast Apple Vision read by a character error rate over 10%, or covers less than 90% of the page's text;
      - it holds a weak table: more than 20% of its cells empty, a cell spanning rows or columns, or a table that continues onto the next page;
      - pictures cover more than half of its area;
      - it is still empty or garbled after OCR;
      - or the user chooses "Re-read this page".

      A re-read that leaves a page with fewer tables, or fewer of the words Apple Vision sees, is refused, and the first reading is kept. A table that continues onto the next page is stitched after reading.

    - **Page-reading models:** `page_reader.py` is the only code that runs the MLX models, with a stand-in on Linux and in the tests, so `hard_pages.py`'s routing and grounding are tested everywhere.
    - **Charts and figures:** a picture's own labels and values, as Docling reads them, are indexed with the picture's box before any model reads it. Charts and other pictures over 5% of the page are then read by the local model chosen for figures (`gemma4:26b` by default) through `agent/models.py`: a chart becomes a table, and a figure gets a description. A picture whose reader isn't installed is marked pending, and is read once it is.
    - **Hidden text:** text in the text layer that doesn't show on the rendered page (clipped, on a hidden layer, or off the page) is left out. Text that OCR read is never left out.
    - **Placing text:** a passage is highlighted only when its words are grounded without doubt. An element's DocTags box counts only when the Apple Vision words inside it match the element's text. Text that comes without boxes (a page a cloud model read) is matched to Apple Vision's words only by a run of at least 5 normalized words that occurs once on the page. Anything else cites an area, never a passage: its element's box, or the whole page. A chart table or figure description is never matched to words; it cites its picture's box.
    - **A page read by a cloud model:** only when the user chooses "Read this page with Cloud AI" on a Standard document, after seeing the cost (`spend.py`). The backend refuses it for a Private document or in Offline mode, and `agent/models.py` asks `policy.py` again at the moment of sending, then writes the egress log. The image goes as data, with no tools. The privacy tests record image payloads as well as text.
    - `unlock.py` is the only code that handles a password: it is used once, in memory, never written to disk and never logged.
  - **Chunk:** Docling's HybridChunker, keeping each element's text and its locator, in chunks of about 512 tokens capped at 1,024. Tables are serialized as Markdown; a table too long for one chunk is split by rows, with its header repeated. Chart tables and figure descriptions are chunked with their picture's box. A locator is a union, one variant per format:
    - PDF rectangles, normalized to `[0..1]` from the top left after crop box and rotation, with a precision: `words`, a passage highlight, or `element` or `page`, an area;
    - a sheet range;
    - a slide;
    - an email message;
    - a DOM anchor;
    - a line range;
    - a card, for list and total answers, which opens the document's card and preview.
  - **Embed, then publish:**
    - One SQLite transaction switches `active_revision_id` and retires the old revision.
    - LanceDB is updated after the commit.
    - Only the active revision is ever searchable.
  - **Organize:** `organize.py`, a plain job, not a graph.
    - By default the local model writes each card with structured output.
    - Until a local model is installed, the card is basic: the title, file type and dates found. It is marked pending, and rewritten when the model is ready.
    - For Standard folders with Cloud organizing turned on, the chosen cloud helper model writes the card.
    - Large backfills go to the provider's batch API at half price, tracked in `message_batches` and polled every few minutes. OpenRouter has none, so its cards go one at a time.
    - Before a result is applied, the job checks the document's current revision and its folder's settings.
    - The user's edits to a card are never overwritten.
- **`agent/`:** the LangGraph Ask graph, a package inside the backend process, not a server of its own.
  - **The Ask graph** is a fixed graph, with model calls in Plan and Answer:
    - **Prepare:** policy, the thread's summary, memories.
    - **Plan:** rewrites the question into a standalone query, with filters, and classifies it:
      - a lookup;
      - a comparison;
      - a list or total, which is answered from a structured query over the card fields of every document in scope. The matching cards go to the model as evidence, each with a card locator. The answer names any document in scope whose card is pending or lacks the field.
    - **Retrieve:** hybrid search, then the reranker. For a comparison, retrieval runs for each selected document, and any document left without evidence is named.
    - **Grade:** one retry, with near-miss titles, when the top score is weak.
    - **Answer:** Claude with `search_result` blocks, or OpenAI, OpenRouter and local models with `<source id="n">` tags and `[n]` markers.
    - **Verify:** drops citations that match no element or card the answer was given, and marks uncited sentences.
    - **Save and remember.**
  - **Durability:** `AsyncSqliteSaver` (from `langgraph-checkpoint-sqlite`) keeps threads in `library.db`.
    - Each turn has a `run_id`, with one active run per thread and idempotent writes.
    - Threads idle for 7 days keep only their latest checkpoint.
  - **Pauses:** `interrupt()` pauses for `private_in_scope` and `deep_cost`, and `Command(resume=...)` continues. Side effects sit after interrupt points. Stop restores the checkpoint from before the turn.
  - **Streaming:** `astream(stream_mode=["updates", "custom"])`, with custom events from `get_stream_writer()`, relayed to the app as SSE.
  - **Memory** (`memory.py`):
    - The `memories` table is scored with NumPy cosine. Prepare loads the pinned preferences (up to 500 tokens) and the top 5 matches.
    - Extraction runs when a thread has been idle for 5 minutes (ADD, UPDATE, DELETE or NOOP, defaulting to NOOP), and keeps each memory's source and sensitivity.
    - Deleting a memory writes a tombstone, regenerates any thread summary that quoted it, and drops it from any prompt being prepared.
    - Recaps expire after 180 days unless pinned.
  - **Structure:** `harness.py` builds and runs the graph. The agent reaches the backend's service modules only through `agent/api.py`, in-process, and models are built only in `agent/models.py`. The backend reaches the agent only through `backend/agent_service.py`.
- **`app/`:** React, Vite and TypeScript, with React Router, `lucide-react` and PDF.js.
  - It reaches the backend only through `app/src/api.ts`, with the app token.
  - SSE streams are read with `fetch` and an `Authorization` header, never with a token in a URL.
  - Rendered answers and emails never load remote images or run HTML.
  - Fonts (Inter and JetBrains Mono) are bundled.
- **`desktop/`:** Tauri 2.
  - **Packaging:** it bundles the built UI, plus python-build-standalone with a `uv`-synced environment in `bundle.resources`, not PyInstaller or `externalBin`.
  - **Running:**
    - It starts the backend in its own process group.
    - It is a single instance, can launch at login, and stays in the menu bar with a global hotkey for quick search.
    - The WebView has a strict Content Security Policy.
  - **Models:** on first run it downloads the search, OCR and page-reading models with progress: the embedder, the reranker, Docling's layout models and picture classifier, and Granite-Docling-258M's MLX build, about 3.6 GB in all (`backend/model_manifest.json` pins each file's revision, size and hash). The local chat model is a separate Ollama download, offered at setup.
  - **Signing:** the checks build an ad-hoc-signed bundle (`codesign -s -`). `node scripts/sign-release.mjs` signs and notarizes with your own Developer ID.
- **`desktop/mcp-bridge/`:** `securefolio-mcp`, a small Rust stdio binary built with the app.
  - It reads `runtime.json`, starts the backend headless through LaunchServices when it isn't running, and forwards each call with the MCP token.
  - It is packaged as a `.mcpb` Desktop Extension (`server.type: "binary"`).
  - Claude Code adds the same binary as a stdio server, because the port and tokens change on every launch.
- **`e2e/`:** Playwright tests against the Vite UI, with a real backend and worker on a temporary vault built from `data/`, and stub models. They run on ports of their own.
- **`data/`:** the synthetic library the tests and the development vault use, made by the generators in `data/build/`. No real document is ever added.
  - At least one fixture per format, each with its expected locators, including a scanned PDF, a rotated page, a password-protected PDF and a dataless placeholder.
  - Hard-page fixtures: born-digital and scanned tables, charts drawn from known numbers, pages with a broken, wrong or partial text layer, pages where grounding should fail, and cropped and rotated pages. Tables are scored by TEDS and exact cell match, charts by values within 2% of the chart's full scale, and routing by the share of bad-text-layer pages caught and of good ones read again.
  - Planted facts, near misses and card fields for the evaluation questions.

## Models

`agent/models.py` builds every chat model with LangChain's `init_chat_model`, using `langchain-anthropic`, `langchain-openai` (for OpenAI, and for OpenRouter at `https://openrouter.ai/api/v1`) and `langchain-ollama`. Every cloud call goes through that module, which asks `policy.py` first and writes the egress log. A model id is `provider:model`. You choose a model per task in Settings, from `backend/model_catalog.py`: Claude's models and OpenAI's short list, with dated prices, and OpenRouter's published list. The table holds the defaults. The three local jobs (answers, cards, figures) each take an Ollama model.

| Task                                                                        | Default model                                                                                     |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Answer (Standard)                                                           | `anthropic:claude-sonnet-5-5`                                                                     |
| Deep                                                                        | `anthropic:claude-opus-5-5`                                                                       |
| Helpers: rewrite, thread summary, memory extraction, Cloud organizing cards | `anthropic:claude-haiku-5-5`                                                                      |
| Private and Offline answers, and default cards                              | `ollama:gemma4:26b` on a Mac with 48 GB or more; `gemma4:12b` from 24 GB; `qwen3.5:9b` below that |
| Figure descriptions and charts                                              | `ollama:gemma4:26b`, which takes images                                                           |
| A page you ask a cloud model to read (Standard only)                        | `anthropic:claude-sonnet-5-5`                                                                     |
| Evaluation judge                                                            | `anthropic:claude-opus-5-5`, never on Private evidence                                            |

- **Some calls use the providers' SDKs directly:**
  - **Batches:** `models.py` uses the Anthropic and OpenAI Python SDKs to submit batches, poll them and fetch their results, because `init_chat_model` has no batch API. It still checks `policy.py` and writes the egress log for these calls.
  - **The key test:** one tiny request with the key, through the provider's SDK. OpenRouter's is its own key check, with no model and no charge.
  - **Answer:** Claude answers use `search_result` blocks with citations on. Each block's `content` is a list of text blocks, one per element, so a citation maps to exact elements and their locators.
- **Structured outputs** write cards and extract memories. They can't be combined with citations in one request.
- **Prompt caching:** the stable prefix is cached. The cost estimate assumes no cache hits.
- **Prices:** in `backend/model_catalog.py`, dated and naming the model ids they cover. OpenRouter's come from its list, stored with its date. A model with no known price is never called. The batch APIs cost half.
- **Ollama** is optional. It is needed for Private and Offline answers, for full local cards, and for figure and chart reading.
  - SecureFolio talks to it only on loopback.
  - It uses only model tags that `/api/tags` shows are installed locally.
  - Offline mode can be switched on only once the local model is installed.
  - `OLLAMA_NO_CLOUD=1` is a setting of the Ollama server, not of its clients. Setup checks for it and asks you to set it.

## Python, Node and Rust

- **Python:** 3.13 with uv, all in the root `pyproject.toml`, in two dependency groups:
  - `app`, everything the backend and the worker import, since they ship together;
  - `dev`.

  `npm run check:app` checks that both entry points import with `app` alone. Mac-only packages carry a `sys_platform == 'darwin'` marker: `ocrmac`, and the MLX packages, whose marker adds `platform_machine == 'arm64'`.

- **Node:** 22.12 or later.
- **Rust:** stable, for `desktop/` and the bridge.
- **Lockfiles:** `uv.lock`, `package-lock.json` and each `Cargo.lock` are committed, and decide the versions.

## Platforms

The product is a macOS app for Apple Silicon. The checks run on macOS and Linux.

- Each Mac-only capability sits behind one module, with a stand-in that the tests and Linux use:
  - Apple Vision OCR;
  - the MLX page-reading models (`page_reader.py`);
  - the Keychain;
  - dataless flags;
  - the sleep assertion;
  - LaunchServices.
- On Linux, Cargo checks the bridge only. The Tauri shell is built and checked only on macOS.

## Settings

- The installed app reads no `.env`. Cloud keys are in the Keychain, and everything else is in Settings.
- In development, `.env` holds:
  - `ANTHROPIC_API_KEY`, and optionally `OPENAI_API_KEY` and `OPENROUTER_API_KEY`, for live runs only;
  - `SECUREFOLIO_VAULT`, the development vault (`./.vault-dev` by default, built from `data/` by `npm run seed`);
  - optionally, `LOCAL_MODEL` and `OLLAMA_HOST`.
- The checks never read `.env`, never reach the network, and never touch any folder outside the repo and their own temporary vaults.
