
import argparse
import hashlib
import json
import re
from pathlib import Path

import pymupdf


READING_PAGES = list(range(17, 30))
ANSWER_PAGE = 119

EXPECTED_HEADINGS = {
    17: r"READING\s+PASSAGE\s+1",
    21: r"READING\s+PASSAGE\s+2",
    26: r"READING\s+PASSAGE\s+3",
}


def file_hash(path):
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def extract_page(document, page_number):
    page = document[page_number - 1]

    text = page.get_text("text", sort=False)
    raw = page.get_text("dict", sort=False)

    lines = []

    for block_index, block in enumerate(raw["blocks"]):
        if block.get("type") != 0:
            continue

        for line_index, line in enumerate(block["lines"]):
            spans = line.get("spans", [])
            content = "".join(
                span.get("text", "") for span in spans
            )

            if not content.strip():
                continue

            lines.append({
                "block_index": block_index,
                "line_index": line_index,
                "text": content,
                "bbox": [
                    round(v, 2) for v in line["bbox"]
                ],
                "spans": [
                    {
                        "text": span["text"],
                        "font": span["font"],
                        "size": round(span["size"], 2),
                        "bbox": [
                            round(v, 2)
                            for v in span["bbox"]
                        ],
                    }
                    for span in spans
                ],
            })

    return {
        "pdf_page": page_number,
        "width": round(page.rect.width, 2),
        "height": round(page.rect.height, 2),
        "text": text,
        "lines": lines,
        "text_chars": len(text.strip()),
        "word_count": len(page.get_text("words")),
        "line_count": len(lines),
        "needs_ocr_review": len(text.strip()) < 100,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--pdf",
        default="Cambridge 21.pdf",
    )
    parser.add_argument(
        "--out",
        default="output/stage1",
    )

    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    output = Path(args.out).resolve()
    pages_folder = output / "pages"

    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    pages_folder.mkdir(parents=True, exist_ok=True)

    manifest = {
        "source_file": pdf_path.name,
        "source_sha256": file_hash(pdf_path),
        "reading_pages": READING_PAGES,
        "answer_page": ANSWER_PAGE,
        "pages": [],
        "warnings": [],
    }

    with pymupdf.open(pdf_path) as document:
        required_pages = max(
            READING_PAGES + [ANSWER_PAGE]
        )

        if len(document) < required_pages:
            raise ValueError(
                f"PDF has only {len(document)} pages; "
                f"expected at least {required_pages}"
            )

        print(f"PDF pages: {len(document)}")
        print("Extracting Reading Test 1...\n")

        for number in READING_PAGES + [ANSWER_PAGE]:
            data = extract_page(document, number)

            role = (
                "answer_key"
                if number == ANSWER_PAGE
                else "reading"
            )

            heading_found = None

            if number in EXPECTED_HEADINGS:
                normalized = re.sub(
                    r"\s+", " ", data["text"]
                ).upper()

                heading_found = bool(
                    re.search(
                        EXPECTED_HEADINGS[number],
                        normalized,
                    )
                )

            filename = f"page_{number:03d}"

            (pages_folder / f"{filename}.json").write_text(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            (pages_folder / f"{filename}.txt").write_text(
                data["text"],
                encoding="utf-8",
            )

            page_result = {
                "page": number,
                "role": role,
                "chars": data["text_chars"],
                "words": data["word_count"],
                "lines": data["line_count"],
                "needs_ocr_review": data["needs_ocr_review"],
                "expected_heading_found": heading_found,
            }

            manifest["pages"].append(page_result)

            if data["needs_ocr_review"]:
                manifest["warnings"].append(
                    f"Page {number}: low text content"
                )

            if heading_found is False:
                manifest["warnings"].append(
                    f"Page {number}: expected heading missing"
                )

            status = (
                "REVIEW"
                if data["needs_ocr_review"]
                or heading_found is False
                else "OK"
            )

            print(
                f"Page {number:3d} | "
                f"{data['text_chars']:5d} chars | "
                f"{data['line_count']:3d} lines | "
                f"{status}"
            )

    (output / "manifest.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n--- RESULT ---")
    print(f"Pages extracted: {len(manifest['pages'])}")
    print(f"Warnings: {len(manifest['warnings'])}")
    print(f"Output: {output}")

    if manifest["warnings"]:
        print("\nWarnings:")
        for warning in manifest["warnings"]:
            print("-", warning)

    print("\nSTAGE 1 COMPLETE - NOT IMPORT READY")


if __name__ == "__main__":
    main()
