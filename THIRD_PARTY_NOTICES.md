# Third-party notices

SecureFolio's own code is under the Apache License 2.0 (`LICENSE`). It depends on, bundles or downloads the third-party works below, each under its own license. The lockfiles (`uv.lock`, `package-lock.json`, `desktop/*/Cargo.lock`) list every dependency and its exact version, and each package's own license files are its authority.

## Models downloaded on first run

SecureFolio doesn't ship model weights. First run downloads these from Hugging Face, each pinned to a revision in `backend/model_manifest.json`, into your vault:

| Model | Used for | License |
| --- | --- | --- |
| `Qwen/Qwen3-Embedding-0.6B` | Search embeddings | Apache-2.0 |
| `Qwen/Qwen3-Reranker-0.6B` | Reranking answers' passages | Apache-2.0 |
| `docling-project/docling-layout-heron` | Page layout | Apache-2.0 |
| `docling-project/docling-models` | Table structure | CDLA-Permissive-2.0 |
| `docling-project/DocumentFigureClassifier-v2.5` | Picture classes | MIT |
| `ibm-granite/granite-docling-258M-mlx` | Reading hard pages again | Apache-2.0 |

These are the licenses the models' own cards declare at the pinned revisions. Check a model's card before relying on it.

## Local models through Ollama

SecureFolio doesn't ship Ollama or its models. Setup suggests a model for your Mac, and when you choose to download one, SecureFolio asks your Ollama to fetch it (`/api/pull`); Ollama downloads and keeps it, under that model's own license. Run `ollama show <tag> --license` to read it. The suggested `gemma4` and `qwen3.5` tags show the Apache License 2.0 at the time of writing.

## Cloud models

With your own key, SecureFolio sends requests to Anthropic, OpenAI or OpenRouter. Your use of them is under your own agreement with each provider. SecureFolio isn't affiliated with any of them.

## Bundled in SecureFolio.app

The app bundle built by `node scripts/bundle.mjs` contains, among others:

- **Python** from python-build-standalone (Python Software Foundation License, with the licenses of its own parts in the runtime's `LICENSE.txt` and build files).
- **Python packages** from the `app` dependency group in `pyproject.toml`, including PyTorch, Transformers, sentence-transformers, Docling, LanceDB, LangChain, LangGraph, FastAPI, SQLAlchemy, MLX and their dependencies. Each keeps its license files in its `*.dist-info` folder inside the bundle. Most are MIT, BSD or Apache-2.0; `certifi`, `tqdm` and `orjson` include MPL-2.0 code.
- **JavaScript packages** compiled into the UI: React, React Router, `lucide-react` (ISC), `pdfjs-dist` (Apache-2.0) and the Tauri APIs.
- **Fonts:** Inter and JetBrains Mono, from the `@fontsource-variable` packages, under the SIL Open Font License 1.1.
- **Rust crates** compiled into the shell and the MCP bridge, including Tauri (MIT or Apache-2.0).

### Native libraries under the GPL and LGPL

Some Python wheels in the bundle carry native libraries under copyleft licenses:

- `opencv-python` and `opencv-python-headless` (pulled in by `mlx-vlm` and `rapidocr`) include FFmpeg with the x264, x265, libpostproc, rubberband and vidstab libraries, which are under the GPL.
- `pillow-heif`, used to read HEIC images, includes `libx265` (GPL) and `libheif` and `libde265` (LGPL); its wheels' own notice labels them GPLv2.
- `shapely` (pulled in by `rapidocr`) includes GEOS (LGPL-2.1).

Other wheels carry permissive native parts with their own notices, such as PyTorch's and SciPy's bundled components and PDFium's in `pypdfium2`; each wheel's `*.dist-info` folder keeps them.

SecureFolio doesn't use their video or encoding features. They don't affect the license of SecureFolio's source code. Anyone who distributes a built SecureFolio.app must meet those libraries' license terms for that distribution, including offering their source. For this reason no built app is published yet.
