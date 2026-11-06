"""What the installed Docling gives for Markdown, HTML and email. Then converts a
decrypted PDF through a stream, in a child whose temp folder is its own, with every
Python file opened for writing recorded by an audit hook throughout, and the temp
folders denied to native code by a sandbox profile: a write there would fail.
Run from the repo root, as a module: uv run -m scripts.measure.docling_formats"""

import io
import os
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

from backend import telemetry

# Before Docling is imported: Hugging Face reads its telemetry switch then.
telemetry.turn_off()

from docling.datamodel.base_models import DocumentStream  # noqa: E402
from docling.document_converter import DocumentConverter  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from reportlab.pdfgen.canvas import Canvas  # noqa: E402

PROFILE = """(version 1)(allow default)
(deny file-write*
  (subpath "/private/tmp")
  (subpath "/private/var/folders")
  (subpath "/private/var/tmp"))
"""


def show(name: str, document) -> None:
    items = list(document.iterate_items())[:4]
    print(name, [(type(i).__name__, getattr(i, "prov", None)) for i, _ in items])


def formats() -> None:
    converter = DocumentConverter()
    md = io.BytesIO(b"# Title\n\nFirst line.\n\nSecond line.\n")
    show("markdown", converter.convert(DocumentStream(name="a.md", stream=md)).document)
    html = io.BytesIO(b"<h1 id='x'>T</h1><p>Para one.</p>")
    show("html", converter.convert(DocumentStream(name="a.html", stream=html)).document)
    mail = EmailMessage()
    mail["From"] = "a@example.com"
    mail["To"] = "b@example.com"
    mail["Subject"] = "Hello"
    mail.set_content("Body text.\n\nOn a date, B wrote:\n> Quoted.\n")
    eml = io.BytesIO(mail.as_bytes())
    show("email", converter.convert(DocumentStream(name="a.eml", stream=eml)).document)


def unlocked_pdf() -> io.BytesIO:
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(72, 700, "plaintext marker zebra quartz")
    canvas.drawString(72, 650, "a ruling line makes it take the full pipeline")
    canvas.line(72, 640, 400, 640)
    canvas.save()
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(buffer.getvalue())))
    writer.encrypt("pw")
    locked = io.BytesIO()
    writer.write(locked)
    reader = PdfReader(io.BytesIO(locked.getvalue()))
    reader.decrypt("pw")
    plain = io.BytesIO()
    PdfWriter(clone_from=reader).write(plain)
    plain.seek(0)
    return plain


def child() -> None:
    written: list[str] = []
    # The negative control: the temp folder really is denied.
    probe = Path(os.environ["TMPDIR"]) / "securefolio-sandbox-control"
    try:
        probe.write_text("x", encoding="utf-8")
    except PermissionError:
        print("control: a temp file was denied", probe)
    else:
        probe.unlink()
        raise SystemExit(f"control failed: could write {probe}")

    def hook(event: str, args: tuple) -> None:
        if event == "open" and isinstance(args[1], str) and set(args[1]) & set("wax+"):
            written.append(str(args[0]))

    converter = DocumentConverter()
    converter.convert(DocumentStream(name="warm.pdf", stream=unlocked_pdf()))
    sys.addaudithook(hook)
    converter.convert(DocumentStream(name="locked.pdf", stream=unlocked_pdf()))
    outside_cache = [path for path in written if ".cache/models" not in path]
    print("converted under the sandbox; Python files written:", outside_cache)


def main() -> None:
    if sys.argv[1:] == ["child"]:
        child()
        return
    formats()
    profile = Path(".cache/no-temp.sb")
    profile.write_text(PROFILE, encoding="utf-8")
    module = "scripts.measure.docling_formats"
    command = [
        "sandbox-exec",
        "-f",
        str(profile),
        sys.executable,
        "-m",
        module,
        "child",
    ]
    subprocess.run(command, check=True)  # noqa: S603


if __name__ == "__main__":
    main()
