"""`npm run eval -- --rejudge <run> --judge-model <id>`: a finished, judged kept run
judged again by another judge, from its own rows, as a new kept run. It asks only the
judge, never the model that answered, and says claim by claim where the two judges
differ. Only a run on the calibration half, over the synthetic library, is judged
again, and only the claims its first judge read: Private evidence never reached a
judge, and still doesn't."""

import json
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from agent import models
from backend import agent_service, database, keychain, model_catalog, spend, states
from backend.migrations import run as migrations
from evals import compare, figures, run, score, steps
from evals import kept as keeping

CALIBRATION = compare.CALIBRATION


def _question(question_id: str) -> dict[str, Any]:
    return next(
        q for q in json.loads(CALIBRATION.read_text()) if q["id"] == question_id
    )


class CantRejudge(SystemExit):
    """This kept run can't be judged again."""


def first_judge(manifest: dict[str, Any]) -> str:
    """The judge a kept run had: Claude Opus 5.5 before runs named theirs."""
    return str(manifest.get("judge_model") or models.JUDGE_MODEL)


def checked(old: keeping.Kept, judge: str) -> dict[str, Any]:
    manifest = old.manifest
    name = old.folder.name
    if manifest.get("state") != keeping.FINISHED or old.report() is None:
        raise CantRejudge(f"{name} isn't a finished run.")
    if not manifest.get("judged"):
        raise CantRejudge(f"{name} wasn't judged: only a judged run is judged again.")
    if manifest.get("question_set") != compare.calibration_set():
        raise CantRejudge(f"{name} isn't on the calibration half.")
    if first_judge(manifest) == judge:
        raise CantRejudge(f"{name} was judged by {judge} already.")
    return manifest


def _read(claim: dict[str, Any]) -> bool:
    """A claim its judge gave a verdict on: one left for the hand check wasn't read."""
    return isinstance(claim.get("supported"), bool)


def judged_again(row: dict[str, Any]) -> bool:
    """A row whose claims are judged again: one its first judge read. A Private row
    never reached a judge, so it has no claims to line up with its sentences."""
    return not row.get("private") and any(_read(c) for c in row.get("claims") or [])


def _document(claim: dict[str, Any], row: dict[str, Any]) -> str:
    """The document a kept claim cites, whose sensitivity policy reads as it is now.
    A claim keeps its document's id. One kept before claims did is the one document
    of its title among what the answer was handed and its list's rows; where none or
    two have it, the claim is refused, never sent under another's id. A document is
    never told by what its text holds: a passage cut for the model, or one two
    documents share, would tell it wrong."""
    if claim.get("document_id"):
        return str(claim["document_id"])
    written = row.get("written") or {}
    title = claim["title"]
    named = {
        entry.get("document_id", "")
        for entry in written.get("handed") or []
        if entry.get("title") == title
    }
    named |= {
        card.document_id
        for card in run.card_sources({"table": written.get("table")})
        if card.title == title
    }
    named.discard("")
    if len(named) != 1:
        raise CantRejudge(
            f"{row['id']}: a claim citing {title} keeps no document, and its title "
            "names none or several, so it isn't sent."
        )
    return named.pop()


def _cited_claims(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Every claim of the row the judge reads with its passage, as a target, a
    sibling or a source."""
    return [
        c
        for c in row.get("claims") or []
        if not c.get("uncited") and "title" in c and "passage" in c
    ]


def _cited(claim: dict[str, Any], row: dict[str, Any]) -> models.Cited:
    """A judged claim's citation as the judge read it. It was Standard, or policy would
    have refused it to the first judge; policy reads its document as it is now."""
    return models.Cited(
        claim["title"],
        claim["words"],
        claim["passage"],
        _document(claim, row),
        "standard",
    )


def _sources(row: dict[str, Any]) -> list[models.Cited]:
    """What an uncited sentence is judged against: every citation the answer made, as
    `_judged` reads them, and its list's cards."""
    seen: dict[tuple[str, str, str], models.Cited] = {}
    for claim in _cited_claims(row):
        cited = _cited(claim, row)
        # Each document kept, even where two give the same words: policy reads them all.
        seen.setdefault((cited.title, cited.passage, cited.document_id), cited)
    cards = run.card_sources({"table": (row.get("written") or {}).get("table")})
    return [*seen.values(), *cards]


def _rests_on(row: dict[str, Any]) -> set[str]:
    """Every document a row judged again rests on: each of its claims', each the
    answer was handed, and its list's rows, counted or not."""
    written = row.get("written") or {}
    table = written.get("table") or {}
    found = {_document(claim, row) for claim in _cited_claims(row)}
    found |= {entry.get("document_id", "") for entry in written.get("handed") or []}
    found |= {
        r["document_id"] for r in [*table.get("rows", []), *table.get("pending", [])]
    }
    found.discard("")
    return found


def _private_now(documents: set[str]) -> str | None:
    """The title of one of these documents that is Private now, or None."""
    if not documents:
        return None
    with database.read() as connection:
        return connection.execute(
            sa.select(database.documents.c.title)
            .where(
                database.documents.c.id.in_(sorted(documents)),
                database.documents.c.sensitivity == states.PRIVATE,
            )
            .limit(1)
        ).scalar()


def groups(row: dict[str, Any]) -> list[list[int]]:
    """Which claims were judged together, sentence by sentence, as `run._judged` made
    them: a cited sentence's claims, one for each citation the answer kept, judged
    with the others as siblings; and an uncited sentence's one claim, when the answer
    had anything to judge it against. Two sentences with the same words stay apart."""
    known = {c["n"] for c in row.get("citations") or []}
    table = (row.get("written") or {}).get("table")
    sources = bool(known) or bool(run.card_sources({"table": table}))
    found: list[list[int]] = []
    at = 0
    for part in row.get("sentences") or []:
        cited = [n for n in part.get("cite") or [] if n in known]
        size = len(cited) or int(bool(part.get("text", "").strip()) and sources)
        if size:
            found.append(list(range(at, at + size)))
            at += size
    if at != len(row.get("claims") or []):
        raise CantRejudge(
            f"{row['id']}: its claims don't line up with its sentences, so which "
            "were judged together can't be told."
        )
    return found


def _stopped_row(row: dict[str, Any], again: list[dict[str, Any]], done: set[int]):
    """A row the limit stopped partway: the claims judged again keep their new
    verdicts; the others the first judge read lose theirs, kept apart as the first
    judge's, so none can pass for judged again. Never scored, as a stopped row isn't."""
    claims = [
        claim
        if at in done or not _read(claim)
        else {
            **{k: v for k, v in claim.items() if k != "supported"},
            "not_rejudged": True,
            "first_supported": claim["supported"],
        }
        for at, claim in enumerate(again)
    ]
    return {
        **row,
        "claims": claims,
        "state": score.STOPPED,
        "correct": None,
        "step": None,
    }


def rejudged_row(
    question: dict[str, Any], row: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """The row with every claim its first judge read judged again, each with the same
    siblings or sources it was judged with; the claims whose verdict changed; and
    whether the limit stopped it partway."""
    claims = row.get("claims") or []
    if not judged_again(row):
        return row, [], False
    again = list(claims)
    changed: list[dict[str, Any]] = []
    done: set[int] = set()
    try:
        _judge_groups(row, claims, again, changed, done)
    except models.OverLimit:
        return _stopped_row(row, again, done), changed, True
    read = [c["supported"] for c in again if _read(c)]
    sound = all(read) if read else None
    rejudged = {
        **row,
        "claims": again,
        "correct": run.correct(question, row.get("facts_found"), sound, again),
    }
    return {**rejudged, "step": steps.step(question, rejudged)}, changed, False


def _judge_groups(
    row: dict[str, Any],
    claims: list[dict[str, Any]],
    again: list[dict[str, Any]],
    changed: list[dict[str, Any]],
    done: set[int],
) -> None:
    """Each claim the first judge read, judged again in its sentence's group, into
    `again`, as each verdict comes. Each call names every document the row rests on,
    for policy to read as it is when the call leaves: one made Private while the run
    goes stops it there."""
    rests_on = sorted(_rests_on(row))
    for group in groups(row):
        for at in group:
            claim = claims[at]
            if not _read(claim):
                continue
            if claim.get("uncited"):
                verdict = agent_service.judge_sentence(
                    claim["claim"], _sources(row), None, rests_on=rests_on
                )
            else:
                siblings = [
                    _cited(claims[other], row)
                    for other in group
                    if other != at and "passage" in claims[other]
                ]
                verdict = agent_service.judge_citation(
                    claim["claim"],
                    _cited(claim, row),
                    siblings,
                    None,
                    rests_on=rests_on,
                )
            if verdict != claim["supported"]:
                changed.append(
                    {"question": row["id"], "claim": at, "was": claim["supported"]}
                )
            again[at] = {**claim, "supported": verdict}
            done.add(at)


def expected_usd(old: keeping.Kept, judge: str) -> float:
    """At most: each judge call the run made, its words sent priced at the new judge's
    price, and the most it may write."""
    price = model_catalog.price(judge) or (0.0, 0.0)
    calls = [
        call
        for row in old.rows()
        for call in row.get("calls") or []
        if call.get("purpose") == "eval_judge"
    ]
    sent = sum(int(call.get("input_tokens") or 0) for call in calls)
    written = models.JUDGE_MAX_TOKENS * len(calls)
    return round((sent * price[0] + written * price[1]) / 1_000_000, 4)


FIRST_RUN = ("spent_usd", "limit_usd", "stopped_at_limit")


def rejudge(
    old: keeping.Kept, root: Path, judge: str, limit_usd: float
) -> tuple[keeping.Kept, dict[str, Any]]:
    """The run judged again as a new kept run under `root`, naming the run it came
    from and its judge; the old one is left as it was. The new run is started before
    any call, keeps each row as it's judged, with this run's own judge calls, and is
    kept as stopped if its limit stops it. Returns it, and how far the two judges
    agree."""
    manifest = checked(old, judge)
    rows = old.rows()
    again = [row for row in rows if judged_again(row)]
    # Before anything is sent: every row lines up, and each claim the judge reads is
    # told to its one document.
    for row in again:
        groups(row)
        for claim in _cited_claims(row):
            _document(claim, row)
    # And no row rests on a document that is Private now: whatever way its words
    # would take to the judge, as a claim, a sibling or a source, none goes.
    for row in again:
        if private := _private_now(_rests_on(row)):
            raise CantRejudge(
                f"{row['id']} rests on {private}, which is Private now, so nothing "
                "of it is sent."
            )
    before = {
        k: v for k, v in (old.report() or {}).items() if k not in ("rows", "kept")
    }
    ended = {"state", "started_at", "ended_at", "local_loaded"}
    new_manifest = {k: v for k, v in manifest.items() if k not in ended}
    new_manifest |= {"judge_model": judge, "rejudged_from": old.folder.name}
    new = keeping.start(root, str(manifest["answer_model"]), new_manifest)
    started = spend.spent_this_month()
    done: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    stopped = False
    try:
        with agent_service.recording_calls():
            for row in rows:
                again, differs, stopped = rejudged_row(_question(row["id"]), row)
                # This run's own calls: the judge's, never the first run's, kept
                # for a row the limit stopped partway too.
                again = {**again, "calls": agent_service.take_calls(None)}
                new.row(again)
                done.append(again)
                changed.extend(differs)
                if stopped:
                    break
    except BaseException as failed:
        new.fail(type(failed).__name__)
        raise
    agreement = {
        "first_judge": first_judge(manifest),
        "judge": judge,
        "claims": sum(1 for row in done for c in row.get("claims") or [] if _read(c)),
        "differ": len(changed),
        "changed": changed,
    }
    report = {k: v for k, v in before.items() if k not in FIRST_RUN}
    report |= run.scored(done, True)
    report |= {
        "first_run": {k: before[k] for k in FIRST_RUN if k in before},
        "spent_usd": round(spend.spent_this_month() - started, 4),
        "limit_usd": limit_usd,
        "stopped_at_limit": stopped,
        "kept": str(new.folder),
        "rejudged_from": old.folder.name,
        "agreement": agreement,
    }
    report["figures"] = figures.own_section(new)  # never the old run's
    if stopped:
        new.stop_at_limit({**report, "rows": done})
    else:
        new.finish(
            {**report, "rows": done},
            [row["id"] for row in rows],
            local_loaded=manifest.get("local_loaded"),
        )
    return new, agreement


def rejudged(
    old: keeping.Kept, root: Path, judge: str, limit_usd: float
) -> tuple[keeping.Kept, dict[str, Any]]:
    """As the evaluation runs it: the vault brought up to date to read its settings,
    the keys as a run takes them, and no request past the limit."""
    migrations.upgrade()
    keychain.prefer_environment()  # in development, .env's; else the Keychain's
    with agent_service.spending_limit(limit_usd):
        return rejudge(old, root, judge, limit_usd)


def text_of(new: keeping.Kept, agreement: dict[str, Any]) -> str:
    if new.state == keeping.STOPPED_AT_LIMIT:
        return (
            f"Stopped at the limit, judged again as far as it got: {new.folder}. Of "
            f"the {agreement['claims']} claims judged again, the two judges differ on "
            f"{agreement['differ']}.\n"
        )
    changed = ", ".join(
        f"{c['question']} claim {c['claim']} "
        f"({'supported' if c['was'] else 'unsupported'} before)"
        for c in agreement["changed"]
    )
    return (
        f"Judged again by {agreement['judge']} as {new.folder}.\n"
        f"Of {agreement['claims']} claims {agreement['first_judge']} read, the two "
        f"judges differ on {agreement['differ']}{': ' + changed if changed else ''}.\n"
    )
