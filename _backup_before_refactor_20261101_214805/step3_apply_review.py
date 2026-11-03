
import json
import re
from pathlib import Path

SOURCE = Path("output/stage3/question_candidates_v2.json")
REVIEW = Path("stage3_review.json")
DEST = Path("output/stage3/question_candidates_reviewed.json")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    data = load(SOURCE)
    review = load(REVIEW)

    patches = review["question_groups"]
    applied = set()

    for patch in patches:
        first, last = patch["from"], patch["to"]
        group_id = (first, last)

        group = next(
            (
                g for g in data["groups"]
                if (g["number_from"], g["number_to"]) == group_id
            ),
            None,
        )

        if group is None:
            raise ValueError(f"Group not found: {group_id}")

        if group_id in applied:
            raise ValueError(f"Duplicate review: {group_id}")

        if group["question_type_hint"] != "SUMMARY_WORD_BANK":
            raise ValueError(f"Unexpected group type: {group_id}")

        if patch["source_pdf_page"] not in group["source_pages"]:
            raise ValueError(f"Source page mismatch: {group_id}")

        match = re.search(
            r"list of words,\s*([A-Z])\s*[-–]\s*([A-Z])",
            group["raw_group_text"],
            re.IGNORECASE,
        )

        if not match:
            raise ValueError("Cannot determine option range")

        expected = [
            chr(n)
            for n in range(
                ord(match.group(1).upper()),
                ord(match.group(2).upper()) + 1,
            )
        ]

        options = patch["options"]
        keys = [option["key"] for option in options]

        if keys != expected:
            raise ValueError(
                f"Invalid option keys: {keys}, expected {expected}"
            )

        if any(not option["text"].strip() for option in options):
            raise ValueError("Empty option text")

        group["shared_options"] = options
        group["review_source_page"] = patch["source_pdf_page"]
        group["review_method"] = "PDF_VISUAL_REVIEW"

        group["warnings"] = [
            warning for warning in group["warnings"]
            if warning != "WORD_BANK_LAYOUT_REVIEW"
        ]

        for question in group["questions"]:
            token = question.get("gap_token")

            if not token or token not in (group["render_template"] or ""):
                raise ValueError(
                    f"Missing gap token for Q{question['number']}"
                )

            question["status"] = "REVIEWED_CANDIDATE"
            question["answer_format_hint"] = "CHOICE"

        applied.add(group_id)

    questions = [
        question
        for group in data["groups"]
        for question in group["questions"]
    ]

    numbers = [q["number"] for q in questions]

    if len(data["groups"]) != 8 or numbers != list(range(1, 41)):
        raise ValueError("Invalid group or question coverage")

    unresolved = [
        q["number"] for q in questions
        if q["status"] not in (
            "EXTRACTED_CANDIDATE",
            "REVIEWED_CANDIDATE",
        )
    ]

    warnings = [
        warning
        for group in data["groups"]
        for warning in group["warnings"]
    ]

    data["status"] = "REVIEWED_CANDIDATES_NOT_IMPORT_READY"
    data["answers_mapped"] = False

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n--- STAGE 3 REVIEW ---")
    print("Groups:", len(data["groups"]))
    print("Question records:", len(questions))
    print("Extracted candidates:", sum(
        q["status"] == "EXTRACTED_CANDIDATE"
        for q in questions
    ))
    print("Reviewed candidates:", sum(
        q["status"] == "REVIEWED_CANDIDATE"
        for q in questions
    ))
    print("Unresolved questions:", unresolved)
    print("Remaining warnings:", warnings)
    print("Output:", DEST)
    print("\nNOT IMPORT READY - ANSWER MAPPING REQUIRED")


if __name__ == "__main__":
    main()
