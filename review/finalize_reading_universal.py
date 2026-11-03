
"""Build a review-gated Stage-6 Reading candidate from Stages 1-5.

Never grants approval, mutates previous stages or writes to a database.
Dry-run by default; --write creates a new Stage-6 candidate and report.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


TYPE_MAP = {
    "NOTE_COMPLETION": "note_completion",
    "TABLE_COMPLETION": "table_completion",
    "FLOW_CHART_COMPLETION": "flow_chart_completion",
    "FORM_COMPLETION": "form_completion",
    "SENTENCE_COMPLETION": "sentence_completion",
    "SUMMARY_COMPLETION": "summary_completion",
    "SUMMARY_WORD_BANK": "summary_completion",
    "TRUE_FALSE_NOT_GIVEN": "true_false_not_given",
    "YES_NO_NOT_GIVEN": "yes_no_not_given",
    "MATCHING_SENTENCE_ENDINGS": "matching_sentence_endings",
    "MATCHING_INFORMATION": "matching_information",
    "MATCHING_FEATURES": "matching_features",
    "MATCHING_HEADINGS": "matching_headings",
    "MULTIPLE_CHOICE": "mcq_single",
    "MCQ_MULTI": "mcq_multi",
    "MULTIPLE_CHOICE_MULTI": "mcq_multi",
}

INLINE = {
    "NOTE_COMPLETION",
    "TABLE_COMPLETION",
    "FLOW_CHART_COMPLETION",
    "FORM_COMPLETION",
    "SENTENCE_COMPLETION",
    "SUMMARY_COMPLETION",
    "SUMMARY_WORD_BANK",
}

FIXED = {
    "TRUE_FALSE_NOT_GIVEN",
    "YES_NO_NOT_GIVEN",
}

WORD_VALUES = {
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
    "SIX": 6,
}


def ensure(ok, message):
    if not ok:
        raise ValueError(message)


def load(path):
    ensure(path.is_file(), f"Missing input: {path}")

    return json.loads(
        path.read_text(encoding="utf-8-sig")
    )


def sha(path):
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def compact(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()


def read_title(stage1, passage):
    chunks = passage.get("body_chunks") or []

    ensure(
        chunks,
        "Missing passage body chunks",
    )

    page_no = chunks[0]["pdf_page"]

    source = load(
        stage1 / "pages" / f"page_{page_no:03d}.json"
    )

    title_lines = []

    for line in source.get("lines") or []:
        spans = line.get("spans") or []

        size = max(
            (
                span.get("size", 0)
                for span in spans
            ),
            default=0,
        )

        txt = compact(
            line.get("text")
        )

        if (
            size >= 14
            and re.search(r"[A-Za-z]", txt)
        ):
            title_lines.append(txt)

    ensure(
        1 <= len(title_lines) <= 3,
        (
            f"Passage {passage['passage_number']}: "
            f"ambiguous title layout: {title_lines}"
        ),
    )

    title = " ".join(title_lines)

    ensure(
        5 <= len(title) <= 200,
        f"Passage {passage['passage_number']}: invalid title",
    )

    return title, title_lines


def passage_content(passage, title_lines):
    lines = "\n".join(
        chunk["raw_text"]
        for chunk in passage["body_chunks"]
    ).splitlines()

    start = None

    for index in range(len(lines)):
        if compact(lines[index]) != title_lines[0]:
            continue

        pos = index

        for part in title_lines:
            while (
                pos < len(lines)
                and not compact(lines[pos])
            ):
                pos += 1

            if (
                pos == len(lines)
                or compact(lines[pos]) != part
            ):
                break

            pos += 1

        else:
            start = pos
            break

    ensure(
        start is not None,
        (
            f"Passage {passage['passage_number']}: "
            "title not located in text"
        ),
    )

    printed = {
        chunk["pdf_page"] - 1
        for chunk in passage["body_chunks"]
    }

    body = []

    for line in lines[start:]:
        value = line.strip()

        if re.fullmatch(
            r"(?:Test\s+\d+|Reading)",
            value,
            re.I,
        ):
            continue

        if (
            value.isdigit()
            and int(value) in printed
        ):
            continue

        body.append(line.strip())

    content = "\n".join(body).strip()

    ensure(
        len(compact(content)) >= 1500,
        (
            f"Passage {passage['passage_number']}: "
            "too short after cleanup"
        ),
    )

    return content


def word_limit(instructions):
    text = compact(instructions).upper()

    found = re.search(
        r"(?:NO MORE THAN|UP TO|MAXIMUM OF)\s+"
        r"(ONE|TWO|THREE|FOUR|FIVE|SIX|[1-6])"
        r"\s+WORDS?\b",
        text,
    )

    if not found:
        found = re.search(
            r"\b(ONE|TWO|THREE|FOUR|FIVE|SIX|[1-6])"
            r"\s+WORDS?\s+ONLY\b",
            text,
        )

    if not found:
        return None

    token = found.group(1)

    return WORD_VALUES.get(
        token,
        int(token) if token.isdigit() else None,
    )


def split_inline(template):
    marker = re.search(
        r"\bon\s+your\s+answer\s+sheet\s*\.",
        template,
        re.I | re.S,
    )

    ensure(
        marker is not None,
        "Inline instructions missing answer-sheet boundary",
    )

    instructions = template[:marker.end()].strip()
    content = template[marker.end():].strip()

    ensure(
        bool(content),
        "Inline group is missing content",
    )

    return instructions, content


def strip_pdf_footer(text, source_pages):
    """Remove verified PDF running headers and footer lines.

    Only removes:
    - Standalone Reading / Test N headers
    - Printed page numbers matching source PDF pages
    - Standalone PDF page-reference arrows

    Preserves question gaps, prose and diagram arrows.
    """
    printed_pages = {
        page - 1
        for page in source_pages
        if type(page) is int
    }

    cleaned = []
    removed = []

    for line in text.splitlines():
        value = line.strip()

        running_header = bool(re.fullmatch(
            r"(?:Reading|Test\s*\d+)",
            value,
            re.I,
        ))

        page_number = bool(
            re.fullmatch(r"\d{1,3}", value)
            and int(value) in printed_pages
        )

        page_reference = bool(re.match(
            r"^[➔→].*\b(?:p\.?|page)\s*\d+",
            value,
            re.I,
        ))

        if (
            running_header
            or page_number
            or page_reference
        ):
            removed.append(value)
        else:
            cleaned.append(line)

    return "\n".join(cleaned).strip(), removed


def make_group(group):
    first = group["number_from"]
    last = group["number_to"]
    kind = group["question_type_hint"]

    ensure(
        kind in TYPE_MAP,
        f"Q{first}-{last}: unsupported type {kind}",
    )

    raw = group.get("raw_group_text") or ""
    template = group.get("render_template") or ""
    options = group.get("shared_options") or None
    source_pages = group.get("source_pages") or []

    removed_artifacts = []

    if kind in INLINE:
        instructions, content = split_inline(template)

        instructions, removed = strip_pdf_footer(
            instructions,
            source_pages,
        )
        removed_artifacts.extend(removed)

        content, removed = strip_pdf_footer(
            content,
            source_pages,
        )
        removed_artifacts.extend(removed)

        gaps = [
            int(number)
            for number in re.findall(
                r"\{\{gap:(\d+)\}\}",
                content,
            )
        ]

        expected_gaps = list(
            range(first, last + 1)
        )

        ensure(
            gaps == expected_gaps,
            f"Q{first}-{last}: gap mismatch {gaps}",
        )

    else:
        match = re.search(
            rf"(?m)^\s*{first}\s*(?:[.)])?\s*$",
            raw,
        )

        instructions = (
            raw[:match.start()].strip()
            if match
            else raw.splitlines()[0].strip()
        )

        instructions, removed = strip_pdf_footer(
            instructions,
            source_pages,
        )
        removed_artifacts.extend(removed)

        content = None

    limit = (
        word_limit(instructions)
        if kind in INLINE
        else None
    )

    prepared = []
    slots = []

    for question in group["questions"]:
        number = question["number"]
        numbers = question.get("numbers") or [number]
        marks = len(numbers)

        fmt = str(
            question.get("answer_format") or ""
        ).lower()

        ensure(
            fmt in ("text", "choice", "multi_choice"),
            f"Q{number}: invalid answer format {fmt}",
        )

        ensure(
            question.get("correct_answer"),
            f"Q{number}: missing answer",
        )

        ensure(
            numbers == list(range(number, number + marks)),
            f"Q{number}: invalid slots",
        )

        slots.extend(numbers)

        config = dict(question.get("config") or {})

        if fmt == "text":
            ensure(
                limit is not None,
                f"Q{number}: missing word limit",
            )

            config["max_words"] = limit

            accepted = (
                question["correct_answer"].get("accepted")
                or []
            )

            ensure(
                bool(accepted)
                and all(
                    1 <= len(compact(value).split()) <= limit
                    for value in accepted
                ),
                (
                    f"Q{number}: answer exceeds "
                    f"{limit}-word limit"
                ),
            )

        if fmt == "multi_choice":
            ensure(
                marks > 1,
                f"Q{number}: multi-choice requires "
                "multiple marks",
            )

            config["select_count"] = marks

        else:
            ensure(
                marks == 1,
                f"Q{number}: unexpected multiple slots",
            )

        prompt = question.get("prompt")

        if kind not in INLINE:
            ensure(
                isinstance(prompt, str)
                and bool(prompt.strip()),
                f"Q{number}: missing prompt",
            )

        own_options = question.get("options") or None

        item = {
            "number": number,
            "question_type": TYPE_MAP[kind],
            "answer_format": fmt,
            "max_score": marks,
            "prompt": prompt,
            "options": own_options,
            "config": config,
            "correct_answer": question["correct_answer"],
            "grading_config": (
                question.get("grading_config") or {}
            ),
            "render_mode": (
                "inline_gap"
                if kind in INLINE
                else "question_cards"
            ),
        }

        if marks > 1:
            item["numbers"] = numbers

        prepared.append(item)

    ensure(
        slots == list(range(first, last + 1)),
        f"Q{first}-{last}: slot mismatch {slots}",
    )

    if removed_artifacts:
        print(
            f"PDF CLEANUP Q{first}-{last}: "
            f"{removed_artifacts}"
        )

    return {
        "number_from": first,
        "number_to": last,
        "title": f"Questions {first}–{last}",
        "question_type_hint": kind,
        "instructions": instructions,
        "content": content,
        "options": (
            None if kind in FIXED else options
        ),
        "rules": {},
        "questions": prepared,
        "pdf_cleanup_removed_lines": removed_artifacts,
    }

def build(workspace):
    out = workspace / "output"
    stage1 = out / "stage1"

    paths = {
        "manifest": stage1 / "manifest.json",
        "structure": (
            out / "stage2/structure.json"
        ),
        "candidates": (
            out / "stage3/question_candidates_v2.json"
        ),
        "mapped": (
            out / "stage4/reading_mapped.json"
        ),
        "probe": (
            out / "stage4/answer_probe.json"
        ),
    }

    manifest = load(paths["manifest"])
    structure = load(paths["structure"])
    mapped = load(paths["mapped"])

    qa = load(
        out / "stage5/qa_report.json"
    )

    ensure(
        qa.get("status") == "CONTENT_REVIEW_REQUIRED"
        and qa.get("errors") == []
        and qa.get("import_ready") is False,
        "Stage 5 has not passed structural QA",
    )

    for key, path in paths.items():
        ensure(
            (qa.get("source_sha256") or {}).get(key)
            == sha(path),
            f"Input changed after Stage 5: {key}",
        )

    test = manifest.get("test_number")

    ensure(
        type(test) is int and test >= 1,
        "Invalid test_number",
    )

    source_name = manifest.get("source_file", "")

    match = re.search(
        r"Cambridge\s*(\d+)",
        source_name,
        re.I,
    )

    ensure(
        match is not None,
        "Cannot identify Cambridge book number",
    )

    title = (
        f"Cambridge IELTS {match.group(1)} "
        f"- Reading Test {test}"
    )

    passages = structure["passages"]
    groups = mapped["groups"]

    ensure(
        len(passages) == 3,
        "Expected three passages",
    )

    sections = []
    review = list(
        qa.get("review_items") or []
    )

    slot_count = 0
    record_count = 0
    group_count = 0

    for passage in passages:
        no = passage["passage_number"]

        section_title, title_lines = read_title(
            stage1,
            passage,
        )

        content = passage_content(
            passage,
            title_lines,
        )

        review.append(
            f"Passage {no}: verify cleaned "
            "title and content against PDF"
        )

        passage_groups = [
            g
            for g in groups
            if g["passage_number"] == no
        ]

        ensure(
            passage_groups,
            f"Passage {no}: no groups",
        )

        prepared_groups = []

        for group in passage_groups:
            prepared = make_group(group)

            prepared_groups.append(prepared)

            group_count += 1

            record_count += len(
                prepared["questions"]
            )

            slot_count += sum(
                q["max_score"]
                for q in prepared["questions"]
            )

        sections.append({
            "number": no,
            "title": section_title,
            "content": content,
            "question_groups": prepared_groups,
        })

    ensure(
        slot_count == 40
        and qa.get("max_score") == 40
        and record_count == qa.get("question_records")
        and group_count == qa.get("groups"),
        "Stage 6 counts differ from Stage 5",
    )

    dataset = {
        "test": title,
        "type": "reading",
        "status": "CONTENT_REVIEW_REQUIRED",
        "passages": sections,
        "answer_source_page": manifest.get("answer_page"),
    }

    report = {
        "status": "CONTENT_REVIEW_REQUIRED",
        "test": title,
        "passages": 3,
        "groups": group_count,
        "question_records": record_count,
        "question_slots": slot_count,
        "max_score": 40,
        "errors": [],
        "review_items": list(dict.fromkeys(review)),
        "requires_visual_approval": True,
        "import_ready": False,
        "stage5_report_sha256": sha(
            out / "stage5/qa_report.json"
        ),
    }

    return dataset, report


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    stage6 = (
        args.workspace / "output/stage6"
    )

    if args.write:
        ensure(
            not stage6.exists(),
            f"Stage 6 already exists: {stage6}",
        )

    dataset, report = build(
        args.workspace
    )

    print(f"TEST: {dataset['test']}")

    print(
        f"PASSAGES: 3 "
        f"GROUPS: {report['groups']} "
        f"RECORDS: {report['question_records']} "
        f"MARKS: 40"
    )

    print(
        "PDF REVIEW ITEMS:",
        len(report["review_items"]),
    )

    if args.write:
        stage6.mkdir(
            parents=True,
            exist_ok=False,
        )

        for filename, obj in (
            ("reading_dataset.json", dataset),
            ("finalize_report.json", report),
        ):
            (stage6 / filename).write_text(
                json.dumps(
                    obj,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        print(
            "STAGE 6 CANDIDATE CREATED:",
            stage6,
        )

    else:
        print(
            "READ-ONLY PREFLIGHT - NO FILE WRITES"
        )

    print(
        "CONTENT REVIEW REQUIRED - "
        "NO APPROVAL - NO DB WRITES"
    )


if __name__ == "__main__":
    main()
