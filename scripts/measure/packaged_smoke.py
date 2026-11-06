"""Run by the bundle's own Python: sqlite with FTS5, LanceDB, Apple Vision OCR, and
a throwaway Keychain item, as the packaged app will use them."""

import io
import json
import sqlite3
import tempfile
import time

import keyring
import lancedb
from ocrmac import ocrmac
from PIL import Image, ImageDraw

SERVICE = "SecureFolio-smoke-test"

results = {}
connection = sqlite3.connect(":memory:")
connection.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
results["fts5"] = True
db = lancedb.connect(tempfile.mkdtemp())
db.create_table("t", [{"id": 1, "vector": [0.1, 0.2]}])
results["lancedb"] = db.open_table("t").count_rows()
image = Image.new("RGB", (600, 120), "white")
ImageDraw.Draw(image).text(
    (20, 40), "SecureFolio reads this", fill="black", font_size=32
)
buffer = io.BytesIO()
image.save(buffer, "PNG")
found = ocrmac.OCR(Image.open(buffer), recognition_level="accurate").recognize()
results["ocr"] = found[0][0] if found else None
begun = time.time()
keyring.set_password(SERVICE, "test", "throwaway")
results["keychain_read"] = keyring.get_password(SERVICE, "test") == "throwaway"
keyring.delete_password(SERVICE, "test")
results["keychain_seconds"] = round(time.time() - begun, 2)
print(json.dumps(results))
