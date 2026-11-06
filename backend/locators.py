"""Where a citation opens: one variant per format, normalized so a highlight stays
right on rotated and cropped pages."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter


class Rect(BaseModel):
    """[0..1] from the page's top left, after crop box and rotation."""

    x0: float
    y0: float
    x1: float
    y1: float


class PdfLocator(BaseModel):
    kind: Literal["pdf"] = "pdf"
    page: int
    rects: list[Rect]
    precision: Literal["words", "element", "page"]


class SheetLocator(BaseModel):
    kind: Literal["sheet"] = "sheet"
    sheet: str
    range: str


class SlideLocator(BaseModel):
    kind: Literal["slide"] = "slide"
    slide: int
    rects: list[Rect] = []


class MessageLocator(BaseModel):
    kind: Literal["message"] = "message"
    message: int
    start_line: int
    end_line: int


class AnchorLocator(BaseModel):
    kind: Literal["anchor"] = "anchor"
    anchor: str


class LinesLocator(BaseModel):
    kind: Literal["lines"] = "lines"
    start: int
    end: int


class CardLocator(BaseModel):
    kind: Literal["card"] = "card"
    field: str


Locator = Annotated[
    PdfLocator
    | SheetLocator
    | SlideLocator
    | MessageLocator
    | AnchorLocator
    | LinesLocator
    | CardLocator,
    Field(discriminator="kind"),
]
LOCATOR = TypeAdapter(Locator)


def dump(locator: Locator) -> dict:
    return LOCATOR.dump_python(locator, mode="json")


def load(value: dict) -> Locator:
    return LOCATOR.validate_python(value)


def label(locator: Locator) -> str:
    """How a citation names its place."""
    match locator:
        case PdfLocator():
            place = f"Page {locator.page}"
        case SheetLocator():
            place = f"Sheet {locator.sheet}, {locator.range}"
        case SlideLocator():
            place = f"Slide {locator.slide}"
        case MessageLocator():
            place = f"Message {locator.message}"
        case LinesLocator():
            place = (
                f"Line {locator.start}"
                if locator.start == locator.end
                else f"Lines {locator.start}–{locator.end}"
            )
        case CardLocator():
            place = f"Card · {locator.field}"
        case AnchorLocator():
            place = "In the document"
    return place


def clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def rect(box: tuple[float, float, float, float], size: tuple[float, float]) -> Rect:
    """A rectangle in page units from the top left, normalized to the page."""
    left, top, right, bottom = box
    width, height = size
    return Rect(
        x0=clamp(left / width),
        y0=clamp(top / height),
        x1=clamp(right / width),
        y1=clamp(bottom / height),
    )


def rotate_rect(box: Rect, degrees: int) -> Rect:
    """A rectangle in the unrotated page's frame, as seen on the rotated page."""
    match degrees % 360:
        case 90:
            return Rect(x0=1 - box.y1, y0=box.x0, x1=1 - box.y0, y1=box.x1)
        case 180:
            return Rect(x0=1 - box.x1, y0=1 - box.y1, x1=1 - box.x0, y1=1 - box.y0)
        case 270:
            return Rect(x0=box.y0, y0=1 - box.x1, x1=box.y1, y1=1 - box.x0)
        case _:
            return box


WHOLE_PAGE = Rect(x0=0, y0=0, x1=1, y1=1)
