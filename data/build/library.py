"""Builds the synthetic library and its question set. Every name, amount and date
comes from this file's word lists and the seed. The same builder makes the open
calibration half and, with the maintainer's secret seed, the sealed held-out half."""

import io
import json
import random
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import openpyxl
import pillow_heif
from docx import Document as DocxDocument
from msoffcrypto.format.ooxml import OOXMLFile
from PIL import Image, ImageDraw
from pptx import Presentation
from pypdf import PdfReader, PdfWriter
from reportlab import rl_config
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from data.build.hard_pages import chart_image, rasterize, scanned_pdf

READY = True
rl_config.invariant = 1  # the same bytes every run, so recorded output still matches
FIRST_NAMES = [
    "Maren",
    "Tobias",
    "Ines",
    "Rafael",
    "Greta",
    "Omar",
    "Lena",
    "Julian",
    "Priya",
    "Sefton",
]
LAST_NAMES = [
    "Okafor",
    "Lindqvist",
    "Marchetti",
    "Halloran",
    "Novak",
    "Abara",
    "Thorne",
    "Vasquez",
]
TOWNS = ["Bellmont", "Harrowgate", "Ashford", "Kilbride", "Dunmore", "Westerly"]
STREETS = ["Alder", "Birch", "Cedar", "Maple", "Quarry", "Tanner"]
ORGS = [
    "Harbor Mutual",
    "Cascade Motors",
    "Northline Energy",
    "Pinecrest Clinic",
    "Summit Roofing",
    "Lakeview Bank",
    "Orchard Telecom",
    "Ridge Property Group",
]
FILLER = (
    "The parties agree that the terms in this section apply for the full period stated "
    "above, and that notices are given in writing to the addresses on the first page. "
    "Any change to these terms must be agreed in writing by both parties."
)
WIDTH, HEIGHT = letter
FOLDERS = {
    "Home": "standard",
    "Finance": "standard",
    "Scans": "standard",
    "Medical": "private",
    "Taxes": "private",
}
HALF_FILLER_DOCS = 12
TOPIC_FACTS = 7
TOPIC_FINDS = 6


@dataclass
class Question:
    id: str
    category: str
    question: str
    expected_document: str | None = None
    expected_page: int | None = None
    answer: str | None = None
    documents: list[str] = field(default_factory=list)
    private: bool = False
    # For a question across documents: the words a passage from each must hold, and the
    # words the answer must hold.
    passage_facts: dict[str, str] = field(default_factory=dict)
    answer_facts: list[str] = field(default_factory=list)


class Builder:
    def __init__(self, out: Path, seed: int, prefix: str = "") -> None:
        self.out = out
        self.rng = random.Random(seed)  # noqa: S311
        self.prefix = prefix
        self.questions: list[Question] = []
        self.locators: dict[str, dict[str, Any]] = {}
        for folder in FOLDERS:
            (out / folder).mkdir(parents=True, exist_ok=True)

    # ---- values ----

    def person(self) -> str:
        return f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}"

    def org(self) -> str:
        return self.rng.choice(ORGS)

    def money(self, low: int = 50, high: int = 9000) -> str:
        return f"${self.rng.randint(low, high):,}.{self.rng.randint(0, 99):02d}"

    def day(self, start: int = 2023, end: int = 2027) -> str:
        first = date(start, 1, 1)
        chosen = first + timedelta(
            days=self.rng.randint(0, (date(end, 12, 31) - first).days)
        )
        return f"{chosen:%B} {chosen.day}, {chosen.year}"

    def code(self, letters: str) -> str:
        return f"{letters}-{self.rng.randint(100000, 999999)}"

    # ---- writers ----

    def path(self, folder: str, name: str) -> Path:
        return self.out / folder / f"{self.prefix}{name}"

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.out))

    def pdf(self, folder: str, name: str, pages: list[list[str]]) -> str:
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=letter)
        for lines in pages:
            y = HEIGHT - 72
            canvas.setFont("Helvetica", 10)
            for line in lines:
                for piece in _wrapped(line):
                    canvas.drawString(72, y, piece)
                    y -= 14
                y -= 10
            canvas.showPage()
        canvas.save()
        path = self.path(folder, name)
        path.write_bytes(buffer.getvalue())
        return self.relative(path)

    def pages(self, body: list[str], fact: str, at: int, total: int) -> list[list[str]]:
        result = [[FILLER, FILLER] for _ in range(total)]
        result[0] = body
        result[at - 1] = [*result[at - 1], fact]
        return result

    # ---- the documents with planted facts ----

    def lease(self) -> None:
        town, street = self.rng.choice(TOWNS), self.rng.choice(STREETS)
        rent, deposit = self.money(1200, 3200), self.money(200, 600)
        end = self.day(2026, 2027)
        fact = (
            "Pets. Tenant may keep one cat or one small dog with written consent "
            f"and a pet deposit of {deposit}."
        )
        doc = self.pdf(
            "Home",
            "Apartment lease.pdf",
            self.pages(
                [
                    f"Residential lease for {street} Street, {town}.",
                    f"Landlord: {self.org()}.",
                    f"Monthly rent is {rent}.",
                    f"This lease ends on {end}.",
                ],
                fact,
                3,
                4,
            ),
        )
        self.fact("What is the pet deposit on my apartment lease?", doc, 3, deposit)
        self.find("Find my apartment lease", doc)
        self.fact("When does my apartment lease end?", doc, 1, end)
        self.near("What is the parking fee on my apartment lease?", doc)

    def warranty(self) -> None:
        end = self.day(2026, 2029)
        miles = f"{self.rng.randint(3, 10)}0,000 miles"
        fact = f"Basic coverage ends on {end} or at {miles}, whichever comes first."
        doc = self.pdf(
            "Home",
            "Car warranty booklet.pdf",
            self.pages(
                [
                    "New vehicle warranty for a 2024 Ridgeline wagon.",
                    f"Sold by {self.org()}.",
                ],
                fact,
                2,
                3,
            ),
        )
        self.fact("When does the car warranty end?", doc, 2, end)
        self.find("Open the warranty booklet for my car", doc)
        self.near("Does the car warranty cover tire replacement?", doc)

    def insurance(self) -> None:
        names = ["Auto policy", "Renters policy", "Umbrella policy"]
        docs = []
        for name in names:
            premium, number = self.money(150, 1800), self.code("POL")
            doc = self.pdf(
                "Finance",
                f"{name}.pdf",
                [
                    [
                        f"{name} from {self.org()}.",
                        f"Policy number {number}.",
                        f"Annual premium {premium}, for 2025.",
                        FILLER,
                    ]
                ],
            )
            docs.append((doc, premium))
            self.exact(f"What is the policy number on my {name.lower()}?", doc, number)
        premiums = dict(docs)
        self.multi(
            "Compare the annual premiums across my insurance policies.",
            premiums,
            list(premiums.values()),
        )
        self.multi(
            "What did I spend on insurance in 2025?",
            premiums,
            [_sum_of(premiums.values())],
        )

    def receipts(self) -> None:
        for shop, what in (
            ("Tanner Hardware", "a cordless drill"),
            ("Kilbride Appliances", "a dishwasher"),
        ):
            total, number = self.money(40, 1400), self.code("INV")
            doc = self.pdf(
                "Finance",
                f"{shop} receipt.pdf",
                [
                    [
                        f"Receipt from {shop}.",
                        f"Invoice {number} for {what}.",
                        f"Total paid {total}.",
                    ]
                ],
            )
            self.exact(f"How much did I pay for {what}?", doc, total)
            self.find(f"Where's the receipt from {shop}?", doc)

    def reports(self) -> None:
        docs: list[str] = []
        costs: dict[str, str] = {}
        soonest: dict[str, str] = {}
        for name, topic in (
            ("Roof inspection", "roof"),
            ("Energy audit", "insulation"),
            ("Foundation survey", "foundation"),
        ):
            cost, finding = self.money(800, 24000), f"{self.rng.randint(2, 9)} years"
            fact = f"The {topic} needs work within {finding}, at an estimated {cost}."
            doc = self.pdf(
                "Home",
                f"{name} report.pdf",
                self.pages([f"{name} report by {self.org()}.", FILLER], fact, 2, 3),
            )
            docs.append(doc)
            costs[doc], soonest[doc] = cost, finding
            self.fact(
                f"What does the {name.lower()} report estimate the work will cost?",
                doc,
                2,
                cost,
            )
        self.multi(
            "What do the three house reports recommend, and what would it all cost?",
            costs,
            [*costs.values(), _sum_of(costs.values())],
        )
        first = min(docs, key=lambda doc: int(soonest[doc].split()[0]))
        self.multi(
            "Which of the house reports says work is needed soonest?",
            soonest,
            [soonest[first]],
        )

    def statements(self) -> None:
        balances: dict[str, str] = {}
        for month in ("March", "April"):
            balance = self.money(1000, 40000)
            doc = self.pdf(
                "Finance",
                f"Bank statement {month}.pdf",
                [
                    [
                        f"{self.org()} statement for {month} 2026.",
                        f"Closing balance {balance}.",
                    ]
                ],
            )
            balances[doc] = balance
            self.fact(f"What was my closing balance in {month} 2026?", doc, 1, balance)
        march, april = list(balances.values())
        self.multi(
            "How did my closing balance change from March to April 2026?",
            balances,
            [_money(abs(_amount(april) - _amount(march)))],
        )

    def private_docs(self) -> None:
        refund = self.money(100, 9000)
        doc = self.pdf(
            "Taxes",
            "Federal return 2024.pdf",
            [["Federal tax return for 2024.", f"Refund due {refund}."]],
        )
        self.questions.append(
            Question(
                self._id(),
                "private",
                "What refund was due on my 2024 federal return?",
                doc,
                1,
                refund,
                private=True,
            )
        )
        dose = f"{self.rng.randint(5, 40)} mg"
        doc = self.pdf(
            "Medical",
            "Clinic visit.pdf",
            [["Pinecrest Clinic visit summary.", f"Prescribed dose {dose} daily."]],
        )
        self.questions.append(
            Question(
                self._id(),
                "private",
                "What dose was I prescribed at the clinic?",
                doc,
                1,
                dose,
                private=True,
            )
        )

    def hard_answers(self) -> None:
        """Facts answered only in a table, a chart or a page with a bad text layer."""
        amount = self.money(100, 900)
        rows = [
            ["Item", "Cost"],
            ["Gutters", self.money(100, 900)],
            ["Chimney", amount],
        ]
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=letter)
        canvas.drawString(72, 700, "Repair estimate")
        y = 660
        for row in rows:
            canvas.drawString(72, y, row[0])
            canvas.drawString(300, y, row[1])
            canvas.line(72, y - 4, 500, y - 4)
            y -= 22
        canvas.save()
        path = self.path("Home", "Repair estimate.pdf")
        path.write_bytes(buffer.getvalue())
        doc = self.relative(path)
        self.fact(
            "What's the estimated cost for the chimney repair?",
            doc,
            1,
            amount,
            "hard_table",
        )
        fee = self.money(20, 95)
        lines = [f"The late fee for this account is {fee} a month.", FILLER, FILLER]
        image = rasterize(_text_pdf(lines))
        path = self.path("Scans", "Scanned account letter.pdf")
        path.write_bytes(scanned_pdf(image, [["zz qq xx garbled", "layer ~~ ##"]]))
        doc = self.relative(path)
        self.fact(
            "What's the late fee in the scanned account letter?",
            doc,
            1,
            fee,
            "hard_text_layer",
        )

    def more_hard_answers(self) -> None:
        for index, (item, other) in enumerate(
            (("Soffit", "Fascia"), ("Fence", "Gate"))
        ):
            amount = self.money(100, 900)
            buffer = io.BytesIO()
            canvas = Canvas(buffer, pagesize=letter)
            y = 660
            for row in (
                ("Item", "Cost"),
                (other, self.money(100, 900)),
                (item, amount),
            ):
                canvas.drawString(72, y, row[0])
                canvas.drawString(300, y, row[1])
                canvas.line(72, y - 4, 500, y - 4)
                y -= 22
            canvas.save()
            path = self.path("Home", f"Quote {index + 1}.pdf")
            path.write_bytes(buffer.getvalue())
            self.fact(
                f"What does the quote say the {item.lower()} costs?",
                self.relative(path),
                1,
                amount,
                "hard_table",
            )
        image, expected = chart_image(self.rng, "bar")
        label, value = next(iter(expected["values"].items()))
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=letter)
        canvas.drawString(72, 720, "Water use by month, from the utility")
        canvas.drawImage(ImageReader(io.BytesIO(image)), 72, 300, 468, 312)
        canvas.save()
        path = self.path("Home", "Water use chart.pdf")
        path.write_bytes(buffer.getvalue())
        self.fact(
            f"What value does the water use chart show for {label}?",
            self.relative(path),
            1,
            str(value),
            "hard_chart",
        )

    def more_topics(self) -> None:
        """More contracts and letters, each with one fact and a way to find it."""
        made = []
        for index, (name, what) in enumerate(
            (
                ("Internet contract", "monthly charge"),
                ("Gym agreement", "joining fee"),
                ("Storage rental", "security deposit"),
                ("Dentist estimate", "crown cost"),
                ("Vet invoice", "vaccine charge"),
                ("Tuition letter", "term fee"),
                ("Moving quote", "packing charge"),
                ("Solar proposal", "panel cost"),
                ("Garage repair order", "labor charge"),
                ("Piano service note", "tuning fee"),
            )
        ):
            amount = self.money(20, 2500)
            doc = self.pdf(
                "Finance",
                f"{name}.pdf",
                self.pages(
                    [f"{name} from {self.org()}.", FILLER],
                    f"The {what} is {amount}.",
                    2,
                    2,
                ),
            )
            made.append((doc, amount))
            if index < TOPIC_FACTS:
                self.fact(f"What is the {what} in my {name.lower()}?", doc, 2, amount)
            if index < TOPIC_FINDS:
                self.find(f"Pull up my {name.lower()}", doc)
        for first in range(0, 10, 2):
            pair = dict(made[first : first + 2])
            one, two = pair.values()
            self.multi(
                "Which of these two costs more, and by how much?",
                pair,
                [_money(abs(_amount(one) - _amount(two)))],
            )

    def filler(self) -> None:
        for index in range(HALF_FILLER_DOCS):
            name = f"Household notice {index + 1}.pdf"
            self.pdf(
                "Home",
                name,
                [[f"Notice from {self.org()} dated {self.day()}.", FILLER]],
            )

    def unanswerable(self) -> None:
        for text in (
            "What's the serial number of my boat?",
            "When does my passport expire?",
            "What's my gym membership fee?",
            "Who is my dentist?",
            "What did I pay for the piano?",
        ):
            self.questions.append(Question(self._id(), "unanswerable", text))
        for text in (
            "Does the umbrella policy cover flood damage?",
            "What's the late fee on my bank statements?",
            "What does the roof report say about solar panels?",
            "How much was the deposit on the cordless drill?",
            "What's the interest rate on my bank statement?",
            "What does the energy audit say about the swimming pool?",
            "Does the warranty booklet list a roadside assistance number?",
            "What's the cancellation fee on my renters policy?",
        ):
            self.questions.append(Question(self._id(), "near_miss", text))

    # ---- questions ----

    def _id(self) -> str:
        return f"{self.prefix}q{len(self.questions) + 1}"

    def fact(
        self, text: str, doc: str, page: int, answer: str, category: str = "fact"
    ) -> None:
        self.questions.append(Question(self._id(), category, text, doc, page, answer))

    def find(self, text: str, doc: str) -> None:
        self.questions.append(Question(self._id(), "find", text, doc))

    def exact(self, text: str, doc: str, answer: str) -> None:
        self.questions.append(Question(self._id(), "exact", text, doc, None, answer))

    def multi(
        self, text: str, facts: dict[str, str], answer_facts: list[str] | None = None
    ) -> None:
        self.questions.append(
            Question(
                self._id(),
                "multi",
                text,
                documents=list(facts),
                passage_facts=facts,
                answer_facts=answer_facts if answer_facts is not None else [],
            )
        )

    def near(self, text: str, doc: str) -> None:
        self.questions.append(Question(self._id(), "near_miss", text, doc))

    # ---- one fixture per format ----

    def format_fixtures(self) -> None:
        self._mark(
            "Home/Plain text.pdf",
            self.pdf(
                "Home",
                "Plain text.pdf",
                [
                    [
                        "A text PDF holds this planted marker quartz.",
                        "A second paragraph.",
                    ]
                ],
            ),
            {"kind": "pdf", "page": 1},
        )
        scan = self.path("Scans", "Scanned letter.pdf")
        scan.write_bytes(
            scanned_pdf(
                rasterize(
                    _text_pdf(["A scanned letter says the meter reading is 4417."])
                )
            )
        )
        self._mark(None, self.relative(scan), {"kind": "pdf", "page": 1})
        mixed = PdfWriter()
        for part in (
            _text_pdf(["A typed first page."]),
            scanned_pdf(rasterize(_text_pdf(["A scanned second page."]))),
        ):
            mixed.append(PdfReader(io.BytesIO(part)))
        path = self.path("Scans", "Mixed pages.pdf")
        mixed.write(path)
        self._mark(
            None,
            self.relative(path),
            {"kind": "pdf", "page": 2, "text": "scanned second"},
        )
        self._transformed()
        self._locked()
        self._office()
        self._text_formats()
        self._images()
        path = self.path("Home", "Unknown format.xyz")
        path.write_bytes(b"not a format SecureFolio reads")
        self._mark(None, self.relative(path), {"status": "skipped_unsupported"})
        path = self.path("Scans", "In the cloud.pdf")
        path.write_bytes(b"")
        self._mark(
            None,
            self.relative(path),
            {"status": "not_downloaded", "stand_in": "dataless"},
        )

    def _mark(self, _name: str | None, relative: str, expected: dict[str, Any]) -> None:
        self.locators[relative] = expected

    def _transformed(self) -> None:
        phrase = "Clause nine on the transformed page says quartz marker here today."
        # Stored sideways and rotated for display, as landscape pages are.
        buffer = io.BytesIO()
        canvas = Canvas(buffer, pagesize=letter)
        canvas.rotate(90)
        canvas.setFont("Helvetica", 11)
        canvas.drawString(72, -300, phrase)
        canvas.save()
        rotated = PdfWriter(clone_from=PdfReader(io.BytesIO(buffer.getvalue())))
        rotated.pages[0].rotate(90)
        path = self.path("Home", "Rotated page.pdf")
        rotated.write(path)
        self._mark(
            None,
            self.relative(path),
            {"kind": "pdf", "page": 1, "rotate": 90, "text": "quartz"},
        )
        reader = PdfReader(io.BytesIO(_text_pdf([phrase])))
        writer = PdfWriter()
        page = reader.pages[0]
        page.cropbox.lower_left = (36, 100)
        page.cropbox.upper_right = (576, 760)
        writer.add_page(page)
        path = self.path("Home", "Cropped page.pdf")
        writer.write(path)
        self._mark(
            None, self.relative(path), {"kind": "pdf", "page": 1, "text": "quartz"}
        )

    def _locked(self) -> None:
        reader = PdfReader(
            io.BytesIO(_text_pdf(["A locked statement holds the marker basil."]))
        )
        writer = PdfWriter(clone_from=reader)
        writer.encrypt("fixture-pass")
        path = self.path("Finance", "Locked statement.pdf")
        writer.write(path)
        self._mark(
            None,
            self.relative(path),
            {"status": "skipped_password", "password": "fixture-pass"},
        )
        buffer = io.BytesIO()
        document = DocxDocument()
        document.add_paragraph("A locked memo holds the marker juniper.")
        document.save(buffer)
        buffer.seek(0)
        locked = io.BytesIO()
        OOXMLFile(buffer).encrypt("fixture-pass", locked)
        path = self.path("Finance", "Locked memo.docx")
        path.write_bytes(locked.getvalue())
        self._mark(
            None,
            self.relative(path),
            {"status": "skipped_password", "password": "fixture-pass"},
        )

    def _office(self) -> None:
        document = DocxDocument()
        document.add_heading("Moving plan", level=1)
        document.add_paragraph("The van is booked, and the marker fennel says so.")
        path = self.path("Home", "Moving plan.docx")
        document.save(path)
        self._mark(
            None, self.relative(path), {"kind": "anchor", "text": "marker fennel"}
        )
        deck = Presentation()
        for number in (1, 2):
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = f"Budget slide {number}"
            slide.placeholders[
                1
            ].text = f"Dues rise on slide {number}, marker sage{number}."
        path = self.path("Home", "Budget deck.pptx")
        deck.save(path)
        self._mark(
            None,
            self.relative(path),
            {"kind": "slide", "slide": 2, "text": "marker sage2"},
        )
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Bills"
        sheet.append(["Item", "Jan", "Feb"])
        sheet.append(["Rent", 2350, 2350])
        sheet.append(["Marker thyme", 125, 125])
        path = self.path("Finance", "Household budget.xlsx")
        workbook.save(path)
        self._mark(
            None,
            self.relative(path),
            {
                "kind": "sheet",
                "sheet": "Bills",
                "range": "A3:C3",
                "text": "Marker thyme",
            },
        )
        path = self.path("Finance", "Transactions.csv")
        path.write_text(
            "Date,Payee,Amount\n2026-01-02,Marker dill,40.00\n", encoding="utf-8"
        )
        self._mark(
            None,
            self.relative(path),
            {
                "kind": "sheet",
                "sheet": "Sheet1",
                "range": "A2:C2",
                "text": "Marker dill",
            },
        )

    def _text_formats(self) -> None:
        path = self.path("Home", "Moving checklist.md")
        path.write_text(
            "# Checklist\n\n- Book the van\n\nAsk about the marker clove here.\n",
            encoding="utf-8",
        )
        self._mark(
            None,
            self.relative(path),
            {"kind": "lines", "start": 5, "end": 5, "text": "marker clove"},
        )
        path = self.path("Home", "Plain note.txt")
        path.write_text(
            "First line.\nSecond line.\n\nThe marker anise is here.\n", encoding="utf-8"
        )
        self._mark(
            None,
            self.relative(path),
            {"kind": "lines", "start": 4, "end": 4, "text": "marker anise"},
        )
        path = self.path("Home", "Saved page.html")
        path.write_text(
            "<html><body><h1>Saved article</h1><p id='p1'>The marker sorrel is here."
            "<img src='https://example.com/x.png'></p></body></html>",
            encoding="utf-8",
        )
        self._mark(
            None,
            self.relative(path),
            {"kind": "anchor", "anchor": "#p1", "text": "marker sorrel"},
        )
        message = EmailMessage()
        message["From"] = "maren@example.com"
        message["To"] = "owner@example.com"
        message["Subject"] = "Re: lease renewal"
        message.set_content(
            "Happy to renew, marker chervil.\n\nOn May 28, 2026, Owner wrote:\n"
            "> Does the deposit carry over, marker tarragon?\n"
        )
        message.add_attachment(
            b"x", maintype="application", subtype="pdf", filename="terms.pdf"
        )
        path = self.path("Home", "Lease email.eml")
        path.write_bytes(message.as_bytes())
        self._mark(
            None,
            self.relative(path),
            {"kind": "message", "message": 2, "text": "marker tarragon"},
        )

    def _images(self) -> None:
        picture = Image.new("RGB", (900, 200), "white")
        ImageDraw.Draw(picture).text(
            (30, 80), "Receipt total marker borage", fill="black", font_size=36
        )
        pillow_heif.register_heif_opener()
        for name, options in (
            ("Screenshot.png", {}),
            ("Photo.jpeg", {"quality": 90}),
            ("Phone photo.heic", {}),
            ("Web image.webp", {}),
        ):
            path = self.path("Scans", name)
            picture.save(path, **options)
            self._mark(
                None,
                self.relative(path),
                {"kind": "pdf", "page": 1, "text": "marker borage"},
            )

    def build_all(self) -> None:
        self.lease()
        self.warranty()
        self.insurance()
        self.receipts()
        self.reports()
        self.statements()
        self.private_docs()
        self.hard_answers()
        self.more_hard_answers()
        self.more_topics()
        self.filler()
        self.unanswerable()


def _amount(money: str) -> float:
    return float(money.replace("$", "").replace(",", ""))


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _sum_of(amounts: Iterable[str]) -> str:
    return _money(sum(_amount(amount) for amount in amounts))


def _wrapped(line: str, width: int = 90) -> list[str]:
    words, lines, current = line.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return [*lines, current]


def _text_pdf(lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=letter)
    y = HEIGHT - 72
    canvas.setFont("Helvetica", 11)
    for line in lines:
        for piece in _wrapped(line):
            canvas.drawString(72, y, piece)
            y -= 16
    canvas.save()
    return buffer.getvalue()


def write_questions(builder: Builder, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(q) for q in builder.questions], indent=1), encoding="utf-8"
    )


def build_held_out(out: Path, seed: int, questions: Path) -> None:
    builder = Builder(out, seed, prefix="H ")
    builder.build_all()
    write_questions(builder, questions)
    (out / "folders.json").write_text(json.dumps(FOLDERS), encoding="utf-8")
    (out.parent / "locators.json").write_text(
        json.dumps(builder.locators), encoding="utf-8"
    )


def main() -> None:
    root = Path(__file__).parent.parent
    builder = Builder(root / "library", 303)
    builder.build_all()
    builder.format_fixtures()
    write_questions(builder, root / "evals" / "calibration" / "questions.json")
    keys = root / "evals" / "calibration" / "locators.json"
    keys.write_text(json.dumps(builder.locators, indent=1), encoding="utf-8")
    folders = root / "library" / "folders.json"
    folders.write_text(json.dumps(FOLDERS, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
