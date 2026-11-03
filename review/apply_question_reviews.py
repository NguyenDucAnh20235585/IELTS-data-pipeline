
import json
import re
from pathlib import Path

SOURCE = Path("output/stage3/question_candidates_v2.json")
REVIEW = Path("stage3_review.json")
DEST = Path("output/stage3/question_candidates_reviewed.json")
EXPECTED_GROUP_COUNT = 8


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    data = load(SOURCE)
    review = load(REVIEW)
    patches = review["question_groups"]
    applied = set()

    accepted_warnings = {
        "TABLE_COMPLETION": {
            "TABLE_LAYOUT_REVIEW",
        },
        "MULTIPLE_CHOICE_TWO": {
            "UNORDERED_TWO_CHOICE_REQUIRES_GRADING_RULE",
        },
        "SUMMARY_WORD_BANK": {
            "WORD_BANK_LAYOUT_REVIEW",
            "WORD_BANK_START_NOT_FOUND",
        },
    }

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

        if group is None or group_id in applied:
            raise ValueError(
                f"Missing or duplicate review group: {group_id}"
            )

        if patch["source_pdf_page"] not in group["source_pages"]:
            raise ValueError(
                f"Source page mismatch: {group_id}"
            )

        kind = group["question_type_hint"]

        if kind not in accepted_warnings:
            raise ValueError(
                f"Unsupported reviewed group type: {kind}"
            )

        unexpected = (
            set(group["warnings"]) - accepted_warnings[kind]
        )

        if unexpected:
            raise ValueError(
                f"Q{first}-{last}: new warnings need review: "
                f"{unexpected}"
            )

        if kind == "TABLE_COMPLETION":
            template = patch["render_template"]

            for number in range(first, last + 1):
                token = f"{{{{gap:{number}}}}}"

                if template.count(token) != 1:
                    raise ValueError(
                        f"Missing or repeated table gap {number}"
                    )

            group["render_template"] = template

        elif kind == "SUMMARY_WORD_BANK":
            match = re.search(
                r"list of words,\s*([A-Z])\s*[-–]\s*([A-Z])",
                group["raw_group_text"],
                re.IGNORECASE,
            )

            if not match:
                raise ValueError(
                    f"Q{first}-{last}: option range not found"
                )

            keys = [
                chr(i)
                for i in range(
                    ord(match.group(1).upper()),
                    ord(match.group(2).upper()) + 1,
                )
            ]

            options = patch["options"]

            if [o["key"] for o in options] != keys:
                raise ValueError(
                    f"Q{first}-{last}: invalid word bank keys"
                )

            if any(not o["text"].strip() for o in options):
                raise ValueError(
                    f"Q{first}-{last}: empty option text"
                )

            group["shared_options"] = options

            marker = patch.get("trim_after")

            if marker:
                template = group["render_template"]

                if marker not in template:
                    raise ValueError(
                        f"Q{first}-{last}: trim marker not found"
                    )

                group["render_template"] = (
                    template.split(marker, 1)[0].rstrip()
                )

        elif kind == "MULTIPLE_CHOICE_TWO":
            shared = group.get("shared_options") or []

            if [o["key"] for o in shared] != list("ABCDE"):
                raise ValueError(
                    f"Q{first}-{last}: invalid shared options"
                )

            rule = patch["grading_rule"]
            choices = rule["correct_choices"]

            if (
                rule["mode"] != "unordered_unique_choices"
                or len(choices) != 2
                or len(set(choices)) != 2
                or not set(choices).issubset(set("ABCDE"))
            ):
                raise ValueError(
                    f"Q{first}-{last}: invalid grading rule"
                )

            group["grading_rule"] = rule

        for question in group["questions"]:
            number = question["number"]

            if kind in (
                "TABLE_COMPLETION",
                "SUMMARY_WORD_BANK",
            ):
                token = question.get("gap_token")

                if (
                    token != f"{{{{gap:{number}}}}}"
                    or (group["render_template"] or "").count(token) != 1
                ):
                    raise ValueError(
                        f"Q{number}: invalid reviewed gap token"
                    )

            elif not question.get("prompt"):
                raise ValueError(
                    f"Q{number}: missing shared prompt"
                )

            question["status"] = "REVIEWED_CANDIDATE"

            question["answer_format_hint"] = (
                "TEXT"
                if kind == "TABLE_COMPLETION"
                else "CHOICE"
            )

        group["review_source_page"] = patch["source_pdf_page"]
        group["review_method"] = "PDF_VISUAL_REVIEW"
        group["warnings"] = []

        applied.add(group_id)

    questions = [
        q
        for g in data["groups"]
        for q in g["questions"]
    ]

    numbers = [q["number"] for q in questions]

    if (
        len(data["groups"]) != EXPECTED_GROUP_COUNT
        or sorted(numbers) != list(range(1, 41))
    ):
        raise ValueError(
            "Invalid group or question coverage"
        )

    unresolved = [
        q["number"]
        for q in questions
        if q["status"] not in (
            "EXTRACTED_CANDIDATE",
            "REVIEWED_CANDIDATE",
        )
    ]

    warnings = [
        w
        for g in data["groups"]
        for w in g["warnings"]
    ]

    if unresolved or warnings:
        raise ValueError(
            f"Review incomplete: "
            f"unresolved={unresolved}, "
            f"warnings={warnings}"
        )

    data["status"] = "REVIEWED_CANDIDATES_NOT_IMPORT_READY"
    data["answers_mapped"] = False

    DEST.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    DEST.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n--- STAGE 3 REVIEW ---")
    print("Groups:", len(data["groups"]))
    print("Question records:", len(questions))

    print(
        "Extracted candidates:",
        sum(
            q["status"] == "EXTRACTED_CANDIDATE"
            for q in questions
        ),
    )

    print(
        "Reviewed candidates:",
        sum(
            q["status"] == "REVIEWED_CANDIDATE"
            for q in questions
        ),
    )

    print("Unresolved questions:", unresolved)
    print("Remaining warnings:", warnings)
    print("Output:", DEST)

    print(
        "\nNOT IMPORT READY - ANSWER MAPPING REQUIRED"
    )


if __name__ == "__main__":
    main()
