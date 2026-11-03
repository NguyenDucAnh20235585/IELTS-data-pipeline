
import argparse
import json
import re
from pathlib import Path


READING_PAGES = range(17, 30)

EXPECTED_GROUPS = [
    (1, 7), (8, 13),
    (14, 17), (18, 21), (22, 26),
    (27, 30), (31, 36), (37, 40),
]


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def detect_passage(text):
    lines = text.splitlines()

    for index, line in enumerate(lines[:12]):
        match = re.fullmatch(
            r"(?:READING\s+)?PASSAGE\s+([1-3])",
            line.strip(),
            re.IGNORECASE,
        )

        if not match:
            continue

        previous = lines[max(0, index - 3):index]

        if (
            "READING" in line.upper()
            or any(
                item.strip().upper() == "READING"
                for item in previous
            )
        ):
            return int(match.group(1))

    return None


def find_group_headers(text):
    lines = list(re.finditer(
        r"[^\n]*(?:\n|$)", text
    ))

    results = []

    pattern = re.compile(
        r"Questions?\s+(\d{1,2})\s*"
        r"(?:[-–—]|to|and|&)\s*(\d{1,2})",
        re.IGNORECASE,
    )

    range_only = re.compile(
        r"(\d{1,2})\s*(?:[-–—]|to|and|&)"
        r"\s*(\d{1,2})",
        re.IGNORECASE,
    )

    for index, item in enumerate(lines):
        value = item.group().strip()

        match = pattern.fullmatch(value)
        start = item.start()
        end = item.end()

        if not match and value.lower() in (
            "question", "questions"
        ):
            if index + 1 < len(lines):
                next_item = lines[index + 1]
                match = range_only.fullmatch(
                    next_item.group().strip()
                )
                if match:
                    end = next_item.end()

        if not match:
            continue

        first = int(match.group(1))
        last = int(match.group(2))

        if not (1 <= first <= last <= 40):
            continue

        results.append({
            "first": first,
            "last": last,
            "start": start,
            "end": end,
        })

    return results


def detect_question_type(text):
    value = normalize(text).upper()

    if all(
        term in value
        for term in ("TRUE", "FALSE", "NOT GIVEN")
    ):
        return "TRUE_FALSE_NOT_GIVEN"

    if all(
        term in value
        for term in ("YES", "NO", "NOT GIVEN")
    ):
        return "YES_NO_NOT_GIVEN"

    if "COMPLETE THE NOTES" in value:
        return "NOTE_COMPLETION"

    if "COMPLETE THE SUMMARY" in value:
        if "LIST OF WORDS" in value:
            return "SUMMARY_WORD_BANK"
        return "SUMMARY_COMPLETION"

    if "MATCH EACH STATEMENT" in value:
        return "MATCHING_FEATURES"

    if "WHICH SECTION CONTAINS" in value:
        return "MATCHING_INFORMATION"

    if "CHOOSE THE CORRECT LETTER" in value:
        return "MULTIPLE_CHOICE"

    return "UNKNOWN"


def load_pages(folder):
    pages = {}

    for number in READING_PAGES:
        file = folder / "pages" / f"page_{number:03d}.json"

        if not file.is_file():
            raise FileNotFoundError(file)

        data = json.loads(
            file.read_text(encoding="utf-8")
        )

        if data.get("pdf_page") != number:
            raise ValueError(
                f"Page mismatch: {file}"
            )

        pages[number] = data["text"]

    return pages


def build_structure(pages):
    starts = []

    for number, text in pages.items():
        passage = detect_passage(text)

        if passage is not None:
            starts.append((passage, number))

    if [item[0] for item in starts] != [1, 2, 3]:
        raise ValueError(
            f"Unexpected passage headings: {starts}"
        )

    passages = []
    all_groups = []
    warnings = []

    for index, (passage_no, start_page) in enumerate(starts):
        end_page = (
            starts[index + 1][1] - 1
            if index + 1 < len(starts)
            else max(READING_PAGES)
        )

        body_chunks = []
        groups = []

        for page_no in range(start_page, end_page + 1):
            text = pages[page_no]
            headers = find_group_headers(text)

            if not headers:
                chunk = {
                    "pdf_page": page_no,
                    "raw_text": text,
                }

                if groups:
                    groups[-1]["source_chunks"].append(chunk)
                else:
                    body_chunks.append(chunk)

                continue

            
            prefix = text[:headers[0]["start"]].strip()

            if prefix:
                residue = re.sub(
                    r"(?im)^\s*(?:Test\s+\d+|Reading)\s*$",
                    "",
                    prefix,
                ).strip()

                if residue:
                    warnings.append(
                        f"Page {page_no}: unassigned text before "
                        f"Q{headers[0]['first']}: {residue[:80]!r}"
                    )

            for position, header in enumerate(headers):
                next_start = (
                    headers[position + 1]["start"]
                    if position + 1 < len(headers)
                    else len(text)
                )

                fragment = text[
                    header["end"]:next_start
                ].strip()

                group = {
                    "number_from": header["first"],
                    "number_to": header["last"],
                    "source_pages": [page_no],
                    "question_type_hint": detect_question_type(
                        fragment
                    ),
                    "question_numbers": list(range(
                        header["first"],
                        header["last"] + 1,
                    )),
                    "source_chunks": [{
                        "pdf_page": page_no,
                        "raw_text": fragment,
                    }],
                    "status": "NEEDS_QUESTION_PARSING",
                }

                groups.append(group)
                all_groups.append(group)

        for group in groups:
            group["source_pages"] = sorted(set(
                item["pdf_page"]
                for item in group["source_chunks"]
            ))

            if group["question_type_hint"] == "UNKNOWN":
                warnings.append(
                    "Unknown type for Q"
                    f"{group['number_from']}-"
                    f"{group['number_to']}"
                )

        if not body_chunks:
            warnings.append(
                f"Passage {passage_no}: no body text"
            )

        passages.append({
            "passage_number": passage_no,
            "source_pages": [
                item["pdf_page"] for item in body_chunks
            ],
            "body_chunks": body_chunks,
            "question_groups": groups,
        })

    actual = [
        (group["number_from"], group["number_to"])
        for group in all_groups
    ]

    declared = [
        n
        for group in all_groups
        for n in group["question_numbers"]
    ]

    if actual != EXPECTED_GROUPS:
        raise ValueError(
            "Group ranges differ from expected:\n"
            f"Actual: {actual}\n"
            f"Expected: {EXPECTED_GROUPS}"
        )

    if declared != list(range(1, 41)):
        raise ValueError(
            f"Invalid question coverage: {declared}"
        )

    return {
        "status": "STRUCTURE_EXTRACTED_NOT_IMPORT_READY",
        "passages": passages,
        "summary": {
            "passages": len(passages),
            "question_groups": len(all_groups),
            "declared_question_slots": len(declared),
            "question_prompts_parsed": False,
            "warnings": warnings,
        },
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input",
        default="output/stage1",
    )

    parser.add_argument(
        "--out",
        default="output/stage2",
    )

    args = parser.parse_args()

    input_folder = Path(args.input)
    output_folder = Path(args.out)

    pages = load_pages(input_folder)
    result = build_structure(pages)

    output_folder.mkdir(
        parents=True, exist_ok=True
    )

    destination = output_folder / "structure.json"

    destination.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n--- STAGE 2 RESULT ---")

    for passage in result["passages"]:
        no = passage["passage_number"]
        body_pages = passage["source_pages"]

        print(
            f"\nPASSAGE {no} | "
            f"body pages: {body_pages} | "
            f"groups: {len(passage['question_groups'])}"
        )

        for group in passage["question_groups"]:
            print(
                f"  Q{group['number_from']:02d}-"
                f"{group['number_to']:02d} | "
                f"{group['question_type_hint']} | "
                f"pages {group['source_pages']}"
            )

    summary = result["summary"]

    print("\n--- SUMMARY ---")
    print("Passages:", summary["passages"])
    print("Groups:", summary["question_groups"])
    print(
        "Question slots:",
        summary["declared_question_slots"],
    )
    print("Warnings:", len(summary["warnings"]))
    print("Output:", destination)
    print("\nSTAGE 2 COMPLETE - NOT IMPORT READY")


if __name__ == "__main__":
    main()
