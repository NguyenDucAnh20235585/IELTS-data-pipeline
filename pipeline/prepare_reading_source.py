
"""Universal Cambridge-style Reading Stage 1/2 extractor.

Detects Reading sections, answer-key pages, question groups
and question-type candidates. No database writes.
"""

import argparse
import json
import re
from pathlib import Path

import pymupdf


PASSAGE = re.compile(
    r"^(?:TEST\s+\d+\s+)?"
    r"(?:READING\s+){1,3}PASSAGE\s+([123])\b",
    re.I,
)

GROUP = re.compile(
    r"(?im)^[ \t]*Questions?[ \t]+(\d{1,2})"
    r"(?:[ \t]*(?:[-–—]|to|and)[ \t]*(\d{1,2}))?"
    r"[ \t]*$",
    re.I,
)

GROUP_SPLIT = re.compile(
    r"(?im)^[ \t]*Questions?[ \t]*\n[ \t]*"
    r"(\d{1,2})[ \t]*(?:[-–—]|to|and)"
    r"[ \t]*(\d{1,2})[ \t]*$",
    re.I,
)

KEY = re.compile(
    r"^LISTENING AND READING ANSWER KEYS "
    r"TEST\s*(\d+)\s+(?:I\s+)?READING\b",
    re.I,
)

WRITING = re.compile(
    r"WRITING\s+TASK\s+1",
    re.I,
)


def heading(text):
    return " ".join(text[:600].split()).upper()


def find_test(texts, test):
    starts = []

    for number, text in enumerate(texts, 1):
        match = PASSAGE.search(heading(text))

        if match and match.group(1) == "1":
            starts.append(number)

    if not 1 <= test <= len(starts):
        raise ValueError(
            f"Test {test} not found; "
            f"detected {len(starts)} Reading tests"
        )

    start = starts[test - 1]

    boundary = (
        starts[test]
        if test < len(starts)
        else len(texts) + 1
    )

    writing = next(
        (
            number
            for number in range(start + 1, boundary)
            if WRITING.search(heading(texts[number - 1]))
        ),
        None,
    )

    if writing is None:
        raise ValueError(
            "Writing boundary not found"
        )

    end = writing - 1
    passages = []

    for number in range(start, end + 1):
        match = PASSAGE.search(
            heading(texts[number - 1])
        )

        if match:
            passages.append((
                int(match.group(1)),
                number,
            ))

    if [number for number, _ in passages] != [1, 2, 3]:
        raise ValueError(
            f"Invalid passage headings: {passages}"
        )

    keys = []

    for number, text in enumerate(texts, 1):
        match = KEY.match(heading(text))

        if match and int(match.group(1)) == test:
            keys.append(number)

    if len(keys) != 1:
        raise ValueError(
            f"Expected one answer-key page: {keys}"
        )

    return start, end, passages, keys[0]


def classify(raw):
    text = " ".join(raw.upper().split())

    if all(
        value in text
        for value in ("TRUE", "FALSE", "NOT GIVEN")
    ):
        return "TRUE_FALSE_NOT_GIVEN"

    if all(
        value in text
        for value in ("YES", "NO", "NOT GIVEN")
    ):
        return "YES_NO_NOT_GIVEN"

    if "COMPLETE THE NOTES" in text:
        return "NOTE_COMPLETION"

    if "COMPLETE THE TABLE" in text:
        return "TABLE_COMPLETION"

    if (
        "COMPLETE THE FLOW-CHART" in text
        or "COMPLETE THE FLOW CHART" in text
    ):
        return "FLOW_CHART_COMPLETION"

    if "COMPLETE THE SUMMARY" in text:
        return (
            "SUMMARY_WORD_BANK"
            if "LIST OF WORDS" in text
            else "SUMMARY_COMPLETION"
        )

    if (
        "COMPLETE EACH SENTENCE "
        "WITH THE CORRECT ENDING"
    ) in text:
        return "MATCHING_SENTENCE_ENDINGS"

    if (
        "WHICH PARAGRAPH CONTAINS" in text
        or "WHICH SECTION CONTAINS" in text
    ):
        return "MATCHING_INFORMATION"

    if (
        "MATCH EACH STATEMENT" in text
        or "MATCH EACH" in text
    ):
        return "MATCHING_FEATURES"

    if "CHOOSE TWO LETTERS" in text:
        return "MCQ_MULTI"

    if "CHOOSE THE CORRECT LETTER" in text:
        return "MULTIPLE_CHOICE"

    return "UNKNOWN"


def make_structure(texts, starts, end):
    passages = []
    all_numbers = []

    for index, (passage_no, start) in enumerate(starts):
        stop = (
            starts[index + 1][1] - 1
            if index + 1 < len(starts)
            else end
        )

        body = []
        groups = []

        for page_no in range(start, stop + 1):
            text = texts[page_no - 1]

            matches = sorted(
                [
                    *GROUP.finditer(text),
                    *GROUP_SPLIT.finditer(text),
                ],
                key=lambda match: match.start(),
            )

            deduped = []

            for match in matches:
                if (
                    not deduped
                    or match.start() >= deduped[-1].end()
                ):
                    deduped.append(match)

            matches = deduped

            if not matches:
                chunk = {
                    "pdf_page": page_no,
                    "raw_text": text,
                }

                destination = (
                    groups[-1]["source_chunks"]
                    if groups
                    else body
                )

                destination.append(chunk)
                continue

            prefix = text[:matches[0].start()].strip()

            if prefix and groups:
                groups[-1]["source_chunks"].append({
                    "pdf_page": page_no,
                    "raw_text": prefix,
                })

            elif prefix and not re.fullmatch(
                r"(?:Test\s+\d+|Reading|\d+|\s)+",
                prefix,
                re.I,
            ):
                body.append({
                    "pdf_page": page_no,
                    "raw_text": prefix,
                })

            for position, match in enumerate(matches):
                first = int(match.group(1))
                last = int(match.group(2) or first)

                if not 1 <= first <= last <= 40:
                    raise ValueError(
                        f"Invalid group Q{first}-{last}"
                    )

                next_start = (
                    matches[position + 1].start()
                    if position + 1 < len(matches)
                    else len(text)
                )

                fragment = text[
                    match.end():next_start
                ]

                groups.append({
                    "number_from": first,
                    "number_to": last,
                    "question_numbers": list(
                        range(first, last + 1)
                    ),
                    "source_chunks": [{
                        "pdf_page": page_no,
                        "raw_text": fragment,
                    }],
                    "status": "NEEDS_QUESTION_PARSING",
                })

        for group in groups:
            group["source_pages"] = sorted({
                chunk["pdf_page"]
                for chunk in group["source_chunks"]
            })

            raw = " ".join(
                chunk["raw_text"]
                for chunk in group["source_chunks"]
            )

            group["question_type_hint"] = classify(raw)

            all_numbers.extend(
                group["question_numbers"]
            )

        passages.append({
            "passage_number": passage_no,
            "source_pages": sorted({
                chunk["pdf_page"]
                for chunk in body
            }),
            "body_chunks": body,
            "question_groups": groups,
        })

    if all_numbers != list(range(1, 41)):
        raise ValueError(
            f"Invalid question coverage: {all_numbers}"
        )

    warnings = [
        f"Unknown type Q{g['number_from']}-{g['number_to']}"
        for passage in passages
        for g in passage["question_groups"]
        if g["question_type_hint"] == "UNKNOWN"
    ]

    return {
        "status": "STRUCTURE_EXTRACTED_NOT_IMPORT_READY",
        "passages": passages,
        "summary": {
            "passages": 3,
            "question_groups": sum(
                len(p["question_groups"])
                for p in passages
            ),
            "declared_question_slots": 40,
            "warnings": warnings,
        },
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--pdf",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--test",
        required=True,
        type=int,
    )

    parser.add_argument(
        "--workspace",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.pdf.is_file():
        raise FileNotFoundError(args.pdf)

    with pymupdf.open(args.pdf) as doc:
        texts = [
            page.get_text("text", sort=False)
            for page in doc
        ]

        start, end, starts, answer = find_test(
            texts,
            args.test,
        )

        structure = make_structure(
            texts,
            starts,
            end,
        )

        print(
            f"READING TEST {args.test}: "
            f"pages {start}-{end}; "
            f"answer key page {answer}"
        )

        for passage in structure["passages"]:
            groups = [
                (
                    g["number_from"],
                    g["number_to"],
                    g["question_type_hint"],
                )
                for g in passage["question_groups"]
            ]

            print(
                "PASSAGE",
                passage["passage_number"],
                "GROUPS",
                groups,
            )

        print(
            "TOTAL GROUPS:",
            structure["summary"]["question_groups"],
        )
        print("SLOTS: 40")
        print(
            "WARNINGS:",
            structure["summary"]["warnings"],
        )

        if not args.write:
            print("READ-ONLY PREFLIGHT PASS")
            return

        output = args.workspace / "output"
        stage1 = output / "stage1"
        stage2 = output / "stage2"

        if stage1.exists() or stage2.exists():
            raise FileExistsError(
                "Stage 1/2 already exists. "
                "Refusing overwrite."
            )

        # Reuse existing pipeline page extractor.
        from extract_pdf_layout import (
            extract_page,
            file_hash,
        )

        records = []
        pages_data = []

        for number in [
            *range(start, end + 1),
            answer,
        ]:
            data = extract_page(doc, number)

            pages_data.append((number, data))

            records.append({
                "page": number,
                "role": (
                    "answer_key"
                    if number == answer
                    else "reading"
                ),
                "chars": data["text_chars"],
                "words": data["word_count"],
                "lines": data["line_count"],
                "needs_ocr_review": data[
                    "needs_ocr_review"
                ],
            })

        stage1_pages = stage1 / "pages"

        stage1_pages.mkdir(parents=True)
        stage2.mkdir(parents=True)

        for number, data in pages_data:
            name = f"page_{number:03}"

            (stage1_pages / f"{name}.json").write_text(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            (stage1_pages / f"{name}.txt").write_text(
                data["text"],
                encoding="utf-8",
            )

        manifest = {
            "source_file": args.pdf.name,
            "source_sha256": file_hash(args.pdf),
            "test_number": args.test,
            "reading_pages": list(
                range(start, end + 1)
            ),
            "answer_page": answer,
            "pages": records,
            "warnings": [],
        }

        (stage1 / "manifest.json").write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        (stage2 / "structure.json").write_text(
            json.dumps(
                structure,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            "STAGE 1/2 CREATED:",
            output,
        )
        print("NOT IMPORT READY")


if __name__ == "__main__":
    main()
