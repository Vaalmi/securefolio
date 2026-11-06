"""Small PDFs made for a test."""

import io

from reportlab.pdfgen.canvas import Canvas


def pdf_pages(*texts: str) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    for text in texts:
        canvas.drawString(72, 700, text)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()
