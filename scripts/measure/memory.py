"""Peak memory across both processes, in each configuration the 6 GB limit covers:
A, the backend's query embedder with a worker indexing; B, an Ask's reranker too,
with the worker's embedder given up (backend/model_budget.py); C, those two with
the page reader alone in the worker. Footprints come from `vmmap`, Metal included.
Run from the repo root, as a module: uv run -m scripts.measure.memory"""

import io
import json
import multiprocessing
import os
import subprocess
import threading
import time
from pathlib import Path

from backend import telemetry

# Before Docling and sentence-transformers are imported, here and in each child
# process: Hugging Face reads its telemetry switch then.
telemetry.turn_off()

import pypdfium2  # noqa: E402
import torch  # noqa: E402
from docling.datamodel.accelerator_options import (  # noqa: E402
    AcceleratorDevice,
    AcceleratorOptions,
)
from docling.datamodel.base_models import DocumentStream, InputFormat  # noqa: E402
from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: E402
from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: E402
from sentence_transformers import CrossEncoder, SentenceTransformer  # noqa: E402

from worker import ocr, page_reader  # noqa: E402

ROOT = Path(__file__).parents[2] / "data/evals/calibration/hard_pages"
TEXT_PDFS = sorted((ROOT / "tuning").glob("good_*.pdf"))
SCANS = sorted((ROOT / "tuning").glob("bad_*.pdf"))
EMBEDDER = "Qwen/Qwen3-Embedding-0.6B"
RERANKER = "Qwen/Qwen3-Reranker-0.6B"
HALF = {"dtype": torch.float16}
BATCH = 16


def footprint_gb(pid: int) -> float:
    command = ["vmmap", "--summary", str(pid)]
    out = subprocess.run(command, capture_output=True, text=True, check=False).stdout  # noqa: S603
    for line in out.splitlines():
        if line.startswith("Physical footprint:"):
            value = line.split()[-1]
            unit = {"K": 1 / 2**20, "M": 1 / 2**10, "G": 1}[value[-1]]
            return float(value[:-1]) * unit
    return 0.0


def backend_models(with_reranker, ready, stop) -> None:
    embedder = SentenceTransformer(EMBEDDER, device="mps", model_kwargs=HALF)
    embedder.encode(["a warranty clause about the roof"])
    reranker = None
    if with_reranker:
        reranker = CrossEncoder(RERANKER, device="mps", model_kwargs=HALF)
    ready.set()
    # Answers carry on while the worker works, as they would in use.
    while not stop.wait(0.2):
        embedder.encode(["a warranty clause about the roof"])
        if reranker:
            reranker.predict([("warranty", "a passage " * 100)] * 24, batch_size=4)
        torch.mps.empty_cache()


def index_some(with_embedder: bool) -> dict:
    embedder = None
    if with_embedder:
        embedder = SentenceTransformer(EMBEDDER, device="mps", model_kwargs=HALF)
    options = PdfPipelineOptions(
        accelerator_options=AcceleratorOptions(
            device=AcceleratorDevice.CPU, num_threads=4
        )
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    begun = time.time()
    for path in TEXT_PDFS:
        stream = io.BytesIO(path.read_bytes())
        converter.convert(DocumentStream(name=path.name, stream=stream))
        torch.mps.empty_cache()
    if embedder:
        embedder.encode(["a passage of the usual length " * 30] * 64, batch_size=BATCH)
        torch.mps.empty_cache()
    rate = 60 * len(TEXT_PDFS) / (time.time() - begun)
    begun = time.time()
    for path in SCANS:
        buffer = io.BytesIO()
        pypdfium2.PdfDocument(path)[0].render(scale=2).to_pil().save(buffer, "PNG")
        ocr.read_words(buffer.getvalue())
    return {
        "text_pdfs_per_minute": round(rate, 1),
        "scans_per_minute": round(60 * len(SCANS) / (time.time() - begun), 1),
    }


def read_pages() -> dict:
    pdf = (ROOT / "tuning" / "table_clean_scan.pdf").read_bytes()
    page_reader.read_page(pdf, 1)
    page_reader.read_page(pdf, 1)
    return {}


def _worker(work, arg, queue) -> None:
    queue.put(work(arg) if arg is not None else work())


def run(name: str, with_reranker: bool, work, arg) -> None:
    context = multiprocessing.get_context("spawn")
    ready, stop, queue = context.Event(), context.Event(), context.Queue()
    backend = context.Process(target=backend_models, args=(with_reranker, ready, stop))
    backend.start()
    peak = 0.0
    done = threading.Event()
    worker = context.Process(target=_worker, args=(work, arg, queue))

    def watch() -> None:
        nonlocal peak
        while not done.wait(0.5):
            pids = [backend.pid, worker.pid or None]
            peak = max(peak, sum(footprint_gb(pid) for pid in pids if pid))

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    ready.wait()
    worker.start()
    result = queue.get()
    done.set()
    thread.join()
    worker.join()
    stop.set()
    backend.join()
    print(
        json.dumps({"configuration": name, "peak_gb": round(peak, 2), **result}),
        flush=True,
    )


def main() -> None:
    print("pid", os.getpid())
    run("A: query embedder + indexing", False, index_some, True)
    run(
        "B: embedder, reranker + indexing without its embedder", True, index_some, False
    )
    run("C: embedder, reranker + page reader", True, read_pages, None)


if __name__ == "__main__":
    main()
