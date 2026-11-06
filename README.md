# SecureFolio

A private library of every document you own, on your Mac. SecureFolio watches the folders you choose, finds any file or fact as you type, and answers questions with citations that open the exact page, with the passage highlighted. Claude Desktop and Claude Code can search it too, through MCP.

Your files, the index and everything SecureFolio learns stay on your Mac. Folders you mark Private never reach a cloud model or MCP, and Offline mode answers with a local model and makes no connection off the Mac.

SecureFolio is a native macOS app for Apple Silicon. It is an early, single-user project: expect rough edges, and back up what matters. There is no prebuilt download yet: you build it from source, as below.

## What it does

- **Reads your documents where they are:** PDF (text and scanned), DOCX, PPTX, XLSX, CSV, Markdown, plain text, HTML, PNG, JPEG, HEIC, WEBP and `.eml` email. Scans, tables, charts and pages with a bad hidden text layer are read on your Mac. It never changes, moves or deletes your files.
- **Search as you type:** by words and by meaning, and still exact for invoice numbers, amounts and names. Option-Space opens quick search from anywhere.
- **Answers with citations:** each citation opens at its page, sheet, slide, email or lines, a citation that matches nothing the answer was given is dropped, and a sentence with no citation is marked as uncited. When the library doesn't hold the answer, it says so instead of guessing.
- **Lists and totals** ("which contracts expire this year?") are worked out from every document in scope.
- **Your choice of model:** Anthropic, OpenAI or OpenRouter with your own key, or a local model through [Ollama](https://ollama.com). Without a key, search and local answers still work.
- **Cards, collections, notes, threads and memory**, all editable, with nightly backups.
- **MCP:** a read-only server for Claude Desktop and Claude Code, with a switch for each folder.

## Requirements

- A Mac with Apple Silicon and macOS 13 or later. 16 GB of memory works; 48 GB lets the default local model run.
- About 4 GB of disk for the search, OCR and page-reading models, downloaded on first run, plus Ollama's model if you want local answers.
- To build: [uv](https://docs.astral.sh/uv/), Node 22.12 or later, a stable Rust toolchain (`rustup`) and Xcode's command line tools.

## Build and install

```bash
uv sync
npm install
npm run app
```

`npm run app` builds SecureFolio.app, signed ad hoc for your own Mac, and installs it in `/Applications`. Run it again after any change: it quits SecureFolio if it's open, puts the new build in place of the old one, and opens it again. If anything fails, the old app stays as it was. Your library is never touched. (`node scripts/bundle.mjs` builds without installing.)

To run it on another Mac, build with `node scripts/bundle.mjs`, then sign and notarize it with your own Developer ID: `CODESIGN_IDENTITY=... NOTARY_PROFILE=... node scripts/sign-release.mjs`.

Open the app. First run asks for a key from any of the three providers (or none), downloads the search models, and asks which folders to watch and how sensitive each one is.

Coming from DocSynth, SecureFolio's earlier name? `CHANGELOG.md` says how your library moves over.

For local answers, install Ollama and the model Setup suggests. Please also run `launchctl setenv OLLAMA_NO_CLOUD 1` and restart Ollama, so Ollama itself never sends a request to its cloud models; SecureFolio can't set that for you.

## Use

- **Quick search:** Option-Space from anywhere. In the app, Command-K opens the palette and Command-comma opens Settings.
- **Ask:** type a question and press Return. Command-Return opens the selected result instead.
- **From Claude:** Settings, Privacy and MCP, Install, then choose which folders it can search. For Claude Code: `claude mcp add securefolio -- <path to securefolio-mcp>`, which the last step of first run copies for you.
- **Closing the window** leaves SecureFolio in the menu bar, so MCP keeps working.
- **Restore:** Settings, Backup and import, Restore from backup. A backup runs nightly and the last 7 are kept.

## Develop

```bash
uv sync
npm install
cp .env.example .env
npm run models
npm run seed
npm run dev
```

`npm run models` downloads the search, OCR and page-reading models (about 3.6 GB) into the development vault, `npm run seed` indexes the synthetic library in `data/` with them, and `npm run dev` starts the backend, the worker and the app. For work on the UI alone, `SECUREFOLIO_STUB_MODELS=1 npm run seed` indexes with stand-in models and needs no download; the scans and a few layout-heavy PDFs then show as failed, since only real models can read them. `npm run dev:desktop` runs the Mac shell around them.

- `npm run check` runs every check: ruff, pyright and pytest, Prettier, ESLint, tsc and vitest, cargo fmt, clippy and test, the bundle build on macOS, and the Playwright tests. The checks call no real model and need no network once installed.
- `npm run verify:models` runs the real search, OCR and page-reading models on this Mac.
- `npm run eval` runs the evaluation over the synthetic library with real models and your key. It costs about a dollar a run.

`docs/ARCHITECTURE.md` explains how it fits together and the privacy rules the code keeps. `docs/TECH_STACK.md` has the details. See `CONTRIBUTING.md` before opening a pull request.

## Security

Please report a vulnerability privately, as `SECURITY.md` describes, not in a public issue.

## License

Apache License 2.0, copyright 2026 Vaalmika Platforms Inc. SecureFolio is published and maintained by [Vaalmi](https://github.com/Vaalmi). See `LICENSE` and `NOTICE`. SecureFolio downloads third-party models that come under their own licenses and terms; `THIRD_PARTY_NOTICES.md` lists them.

SecureFolio is not affiliated with Anthropic, OpenAI, OpenRouter, Ollama or Google. Their names are used only to say what SecureFolio works with.
