"""Answers to the main set's question shapes, right and wrong, which the pass check
scored as `main_set_before.json` records before your own set changed it."""


def cite(title, page, n=1):
    return {
        "n": n,
        "title": title,
        "label": f"Page {page}",
        "locator": {"kind": "pdf", "page": page},
        "document_id": "d-" + title,
    }


def said(text, *citations, kind="answer"):
    return {
        "kind": kind,
        "parts": [{"text": text, "cite": [c["n"] for c in citations]}],
        "citations": list(citations),
    }


REFUSED = {"kind": "no_evidence", "parts": [], "citations": []}
LEASE = "Apartment lease"
ROOF, ENERGY, FOUNDATION = (
    "Roof inspection report",
    "Energy audit report",
    "Foundation survey report",
)
ANSWERS = {
    "q1": [
        said("The pet deposit is $299.11.", cite(LEASE, 3)),
        said("The pet deposit is $299.11.", cite(LEASE, 2)),
        said("The pet deposit is $300.", cite(LEASE, 3)),
        REFUSED,
    ],
    "q2": [
        said("It's your lease.", cite(LEASE, 1)),
        said("It's the warranty.", cite("Car warranty booklet", 1)),
        REFUSED,
    ],
    "q3": [
        said("It ends August 3, 2027.", cite(LEASE, 1)),
        said("It ends August 3, 2027.", cite(LEASE, 4)),
    ],
    "q4": [REFUSED, said("Yes, it says so.", cite(LEASE, 2))],
    "q20": [
        said(
            "$14,325.35, $7,854.43 and $7,615.75: $29,795.53 in all.",
            cite(ROOF, 2),
            cite(ENERGY, 2, 2),
            cite(FOUNDATION, 2, 3),
        ),
        said(
            "$14,325.35 and $7,854.43: $29,795.53 in all.",
            cite(ROOF, 2),
            cite(ENERGY, 2, 2),
            cite(FOUNDATION, 2, 3),
        ),
        said(
            "$14,325.35, $7,854.43 and $7,615.75: $29,795.53 in all.",
            cite(ROOF, 2),
            cite(ENERGY, 2, 2),
        ),
    ],
    "q50": [REFUSED, said("It's $5.", cite(LEASE, 1))],
    "f1": [
        said("It ends August 3, 2027.", cite(LEASE, 1)),
        said("It ends August 3, 2028.", cite(LEASE, 1)),
    ],
}
