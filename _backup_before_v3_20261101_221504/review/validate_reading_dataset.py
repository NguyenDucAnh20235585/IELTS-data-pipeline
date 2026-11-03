
import json
import re
from pathlib import Path

STRUCTURE = Path("output/stage2/structure.json")
MAPPED = Path("output/stage4/reading_test1_mapped.json")
OUT = Path("output/stage5")

SPEC = [
    (1, 7, "NOTE_COMPLETION", 19),
    (8, 13, "TRUE_FALSE_NOT_GIVEN", 20),
    (14, 17, "MATCHING_INFORMATION", 23),
    (18, 21, "SUMMARY_COMPLETION", 24),
    (22, 26, "MATCHING_FEATURES", 25),
    (27, 30, "MULTIPLE_CHOICE", 28),
    (31, 36, "SUMMARY_WORD_BANK", 29),
    (37, 40, "YES_NO_NOT_GIVEN", 29),
]

INLINE = {
    "NOTE_COMPLETION",
    "SUMMARY_COMPLETION",
    "SUMMARY_WORD_BANK",
}

CHOICES = {
    "TRUE_FALSE_NOT_GIVEN": ["TRUE", "FALSE", "NOT GIVEN"],
    "YES_NO_NOT_GIVEN": ["YES", "NO", "NOT GIVEN"],
    "MATCHING_INFORMATION": list("ABCDEFG"),
    "MATCHING_FEATURES": list("ABCD"),
    "SUMMARY_WORD_BANK": list("ABCDEFGHI"),
    "MULTIPLE_CHOICE": list("ABCD"),
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def compact(value):
    return re.sub(r"\s+", " ", value or "").strip()


def main():
    structure = load(STRUCTURE)
    data = load(MAPPED)

    errors = []
    warnings = []
    review = ["# Cambridge 21 Reading Test 1 — Content QA", ""]

    if not data.get("answers_mapped"):
        errors.append("Answers have not been mapped")

    passages = structure.get("passages", [])
    groups = data.get("groups", [])

    if len(passages) != 3:
        errors.append(f"Expected 3 passages, found {len(passages)}")

    if len(groups) != 8:
        errors.append(f"Expected 8 groups, found {len(groups)}")

    # Check passage content.
    for passage in passages:
        number = passage["passage_number"]

        body = "\n".join(
            chunk["raw_text"]
            for chunk in passage["body_chunks"]
        )

        pages = passage.get("source_pages", [])

        if len(compact(body)) < 1500:
            errors.append(
                f"Passage {number}: suspiciously short body"
            )

        review.extend([
            f"## Passage {number}",
            f"PDF pages: {pages}",
            "",
            "### Extracted passage",
            "",
            body,
            "",
        ])

    all_numbers = []

    # Check each question group.
    for first, last, expected_type, expected_page in SPEC:
        matches = [
            g for g in groups
            if (g["number_from"], g["number_to"]) == (first, last)
        ]

        if len(matches) != 1:
            errors.append(
                f"Q{first}-{last}: expected one group, found {len(matches)}"
            )
            continue

        group = matches[0]
        kind = group["question_type_hint"]
        questions = group["questions"]
        template = group.get("render_template") or ""

        if kind != expected_type:
            errors.append(
                f"Q{first}-{last}: wrong type {kind}"
            )

        if group.get("source_pages") != [expected_page]:
            warnings.append(
                f"Q{first}-{last}: unexpected source pages "
                f"{group.get('source_pages')}"
            )

        if len(questions) != last - first + 1:
            errors.append(
                f"Q{first}-{last}: wrong question count"
            )

        if group.get("warnings"):
            errors.append(
                f"Q{first}-{last}: unresolved group warnings"
            )

        review.extend([
            f"## Questions {first}-{last}",
            f"Type: {kind}",
            f"PDF page: {expected_page}",
            "",
        ])

        if kind in INLINE:
            tokens = [
                int(n) for n in re.findall(
                    r"\{\{gap:(\d{1,2})\}\}", template
                )
            ]

            if tokens != list(range(first, last + 1)):
                errors.append(
                    f"Q{first}-{last}: incorrect gap sequence {tokens}"
                )

            review.extend([
                "### Completion template",
                "",
                template,
                "",
            ])

        shared = group.get("shared_options") or []

        if shared:
            review.extend(["### Shared options", ""])

            for option in shared:
                review.append(
                    f"- {option['key']}: {option['text']}"
                )

            review.append("")

        for question in questions:
            n = question["number"]
            all_numbers.append(n)

            prompt = compact(question.get("prompt"))
            answer = question.get("correct_answer") or {}
            answer_format = question.get("answer_format")
            options = question.get("options") or shared

            if question.get("status") not in (
                "EXTRACTED_CANDIDATE",
                "REVIEWED_CANDIDATE",
            ):
                errors.append(f"Q{n}: unresolved question")

            if kind in INLINE:
                token = f"{{{{gap:{n}}}}}"

                if template.count(token) != 1:
                    errors.append(f"Q{n}: missing or duplicate gap")

                if question.get("render_mode") != "INLINE_GAP":
                    errors.append(f"Q{n}: wrong render mode")
            else:
                if not prompt:
                    errors.append(f"Q{n}: missing prompt")

                if question.get("render_mode") != "QUESTION_CARD":
                    errors.append(f"Q{n}: wrong render mode")

            if kind in ("NOTE_COMPLETION", "SUMMARY_COMPLETION"):
                accepted = answer.get("accepted", [])

                if answer_format != "TEXT" or not accepted:
                    errors.append(f"Q{n}: invalid text answer")
                elif any(
                    len(compact(value).split()) > 1
                    for value in accepted
                ):
                    errors.append(f"Q{n}: answer exceeds one word")

            else:
                expected_keys = CHOICES.get(kind, [])
                actual_keys = [o["key"] for o in options]

                if actual_keys != expected_keys:
                    errors.append(
                        f"Q{n}: option keys mismatch {actual_keys}"
                    )

                if any(
                    not compact(o.get("text"))
                    for o in options
                ):
                    errors.append(f"Q{n}: empty option text")

                choice = answer.get("choice")

                if (
                    answer_format != "CHOICE"
                    or choice not in expected_keys
                ):
                    errors.append(f"Q{n}: invalid choice answer")

            # Detect obvious PDF footer contamination.
            if re.search(
                r"➔|→|\bp\.\s*118\b",
                prompt,
                re.IGNORECASE,
            ):
                warnings.append(
                    f"Q{n}: possible PDF footer in prompt"
                )

            review.extend([
                f"### Q{n}",
                "",
                f"Prompt: {prompt or '[INLINE GAP]'}",
                "",
            ])

            if question.get("options"):
                for option in question["options"]:
                    review.append(
                        f"- {option['key']}: {option['text']}"
                    )

                review.append("")

            review.append(
                "Answer: " + json.dumps(
                    answer, ensure_ascii=False
                )
            )
            review.append("")

        # OCR artifacts requiring visual verification.
        searchable = template + "\n" + group.get(
            "raw_group_text", ""
        )

        if re.search(r"\bofthe\b", searchable, re.IGNORECASE):
            warnings.append(
                f"Q{first}-{last}: suspicious joined word 'ofthe'"
            )

    if all_numbers != list(range(1, 41)):
        errors.append(
            f"Question numbering invalid: {all_numbers}"
        )

    report = {
        "status": (
            "CONTENT_REVIEW_REQUIRED"
            if not errors else "BLOCKED"
        ),
        "passages": len(passages),
        "groups": len(groups),
        "question_records": len(all_numbers),
        "errors": errors,
        "warnings": warnings,
        "manual_pdf_review_required": True,
    }

    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / "qa_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (OUT / "qa_review.md").write_text(
        "\n".join(review),
        encoding="utf-8",
    )

    print("\n--- STAGE 5 CONTENT QA ---")
    print("Passages:", len(passages))
    print("Groups:", len(groups))
    print("Question records:", len(all_numbers))
    print("Errors:", len(errors))
    print("Warnings:", len(warnings))

    for error in errors:
        print("  ERROR:", error)

    for warning in warnings:
        print("  WARNING:", warning)

    print("\nReport:", OUT / "qa_report.json")
    print("Review:", OUT / "qa_review.md")
    print("Status:", report["status"])
    print("\nMANUAL PDF REVIEW REQUIRED BEFORE IMPORT")


if __name__ == "__main__":
    main()
