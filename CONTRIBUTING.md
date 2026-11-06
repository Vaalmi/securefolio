# Contributing

Thanks for helping. SecureFolio is maintained by Vaalmi, with a small team, so a short note first saves everyone time.

- **Bugs:** open an issue with what you did, what happened, what you expected, and your macOS version and Mac. Leave out anything from your own documents.
- **Security problems:** not in an issue. See `SECURITY.md`.
- **Changes:** for anything beyond a small fix, open an issue first and say what you'd like to change and why, so we can agree on the shape before you build it.

By submitting a contribution you agree that it is licensed under the Apache License 2.0, as its section 5 says.

## Setting up

You need a Mac with Apple Silicon for the app itself; everything but the Mac shell also builds and passes its checks on Linux.

```bash
uv sync
npm install
cp .env.example .env
npm run models
npm run seed
npm run dev
```

`npm install` also installs the pre-commit hook, Playwright's browser and the Python runtime the bundle uses. `npm run models` downloads about 3.6 GB of models for the development vault; `SECUREFOLIO_STUB_MODELS=1 npm run seed` skips them, with stand-in models, if you're only working on the UI; the scans and a few layout-heavy PDFs then show as failed.

## The checks

`npm run check` runs every check, and the pre-commit hook runs the ones for the part you changed. Pull requests must pass it on macOS and Ubuntu in CI.

- The checks call no real model, never read `.env`, and reach no service off the machine. Stub models stand in for every model, cloud and local. Never add a test that calls a real model or the network.
- While you work, run only the tests you're changing, without coverage, for example `uv run pytest backend/tests/test_route.py --no-cov`.
- Never lower a threshold, turn off a lint rule or exclude a file to get a green run. If a rule looks wrong, say so in the pull request.

## Rules for the code

`docs/ARCHITECTURE.md` describes the module boundaries and the privacy rules. The ones that matter most:

- **Privacy comes first.** Every chunk, card, note, message, summary and memory carries the sensitivity of what it came from, and `backend/policy.py` decides what may leave the Mac. A change that touches what reaches a model or MCP needs a test in a `test_privacy_*.py` file proving no Private text reaches the stub cloud model.
- **One way to each service.** Reach the backend, the agent, LanceDB, the Keychain, original files, OCR and the models only through the modules named in `docs/ARCHITECTURE.md`.
- **The backend enforces every rule**, whatever the app, the agent or an MCP client does, and refuses with a status and a one-line reason.
- **Document text is data.** It reaches a model only inside a `search_result` block or a `<source>` tag, never in the instructions.
- **Test data is synthetic.** Never add a real document, or anything from one, to `data/` or a test. The generators in `data/build/` make the fixtures.
- **Use the framework's own way** (FastAPI dependencies, SQLAlchemy Core, LangGraph, Docling, React hooks, Tauri plugins) before writing your own.
- Python: always `uv run`, never `python` directly. TypeScript: strict, never `any`. Rust: no `unwrap()` outside tests.
- Keep it simple, and handle an exception only where there is something useful to do with it. Comments say why, not what.
- The UI follows the existing look: colors and spacing are CSS variables in `app/src/styles.css`, in light and dark. Plain English, US spelling, no emojis.

## Pull requests

- One change per pull request, with its tests.
- Say what it changes and why, and how you checked it. For UI changes, add screenshots in light and dark.
- Keep the commit history readable; the maintainers may squash on merge.
