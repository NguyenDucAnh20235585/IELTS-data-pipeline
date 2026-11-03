
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

CHOICES = {}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def compact(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def validate_ir():
    """Validate reviewed questions against Answer IR. No DB writes."""
    ir = load(Path("output/stage4/answer_ir.json"))
    reviewed = load(Path(
        "output/stage3/question_candidates_reviewed.json"
    ))
    structure = load(STRUCTURE)

    errors = []
    warnings = []

    type_map = {
        "MULTIPLE_CHOICE": "mcq_single",
        "MULTIPLE_CHOICE_TWO": "mcq_multi",
        "MULTIPLE_CHOICE_THREE": "mcq_multi",
        "MULTIPLE_CHOICE_MULTI": "mcq_multi",
        "TRUE_FALSE_NOT_GIVEN": "true_false_not_given",
        "YES_NO_NOT_GIVEN": "yes_no_not_given",
        "MATCHING_HEADINGS": "matching_headings",
        "MATCHING_INFORMATION": "matching_information",
        "MATCHING_FEATURES": "matching_features",
        "MATCHING_SENTENCE_ENDINGS": "matching_sentence_endings",
        "SENTENCE_COMPLETION": "sentence_completion",
        "SUMMARY_COMPLETION": "summary_completion",
        "SUMMARY_WORD_BANK": "summary_completion",
        "NOTE_COMPLETION": "note_completion",
        "TABLE_COMPLETION": "table_completion",
        "FLOW_CHART_COMPLETION": "flow_chart_completion",
        "FORM_COMPLETION": "form_completion",
        "DIAGRAM_LABEL_COMPLETION": "diagram_label_completion",
        "SHORT_ANSWER": "short_answer",
        "MAP_PLAN_LABELLING": "map_plan_labelling",
    }

    passages = structure.get("passages", [])
    groups = reviewed.get("groups", [])
    items = ir.get("answer_items", [])
    answer_refs = ir.get("answers", {})

    expected = list(range(1, 41))
    source_groups = {
        (g["number_from"], g["number_to"]): g
        for g in groups
    }
    structure_groups = [
        (p["passage_number"], g)
        for p in passages
        for g in p.get("question_groups", [])
    ]

    if len(passages) != 3:
        errors.append("Expected exactly 3 passages")

    if len(source_groups) != len(groups):
        errors.append("Duplicate reviewed group ranges")

    if len(structure_groups) != len(groups):
        errors.append("Structure/review group count mismatch")

    all_slots = []
    display = ["# Reading Content QA — Answer IR", ""]

    for passage in passages:
        text = "\n".join(
            chunk["raw_text"]
            for chunk in passage.get("body_chunks", [])
        )

        if len(compact(text)) < 1500:
            errors.append(
                f"Passage {passage['passage_number']}: short body"
            )

        display.extend([
            f"## Passage {passage['passage_number']}",
            f"Pages: {passage.get('source_pages', [])}",
            "",
            text,
            "",
        ])

    # Validate structure, reviewed questions and rendering.
    for passage_number, sg in structure_groups:
        first = sg["number_from"]
        last = sg["number_to"]
        group = source_groups.get((first, last))

        if group is None:
            errors.append(
                f"Q{first}-{last}: missing reviewed group"
            )
            continue

        if group.get("passage_number") != passage_number:
            errors.append(
                f"Q{first}-{last}: wrong passage"
            )

        if group.get("question_type_hint") != sg.get(
            "question_type_hint"
        ):
            errors.append(
                f"Q{first}-{last}: type mismatch"
            )

        if group.get("source_pages") != sg.get("source_pages"):
            errors.append(
                f"Q{first}-{last}: source page mismatch"
            )

        if group.get("warnings"):
            errors.append(
                f"Q{first}-{last}: unresolved warnings"
            )

        questions = group.get("questions", [])
        numbers = [q["number"] for q in questions]

        if numbers != list(range(first, last + 1)):
            errors.append(
                f"Q{first}-{last}: invalid question numbering"
            )

        all_slots.extend(numbers)

        template = group.get("render_template") or ""
        shared = group.get("shared_options") or []

        display.extend([
            f"## Questions {first}-{last}",
            f"Type: {group.get('question_type_hint')}",
            f"PDF: {group.get('source_pages')}",
            "",
            template,
            "",
        ])

        for q in questions:
            number = q["number"]

            if q.get("status") not in (
                "EXTRACTED_CANDIDATE",
                "REVIEWED_CANDIDATE",
            ):
                errors.append(
                    f"Q{number}: unresolved review status"
                )

            if q.get("render_mode") == "INLINE_GAP":
                token = f"{{{{gap:{number}}}}}"

                if (
                    q.get("gap_token") != token
                    or template.count(token) != 1
                ):
                    errors.append(
                        f"Q{number}: invalid inline gap"
                    )

            elif not compact(q.get("prompt")):
                errors.append(
                    f"Q{number}: missing prompt"
                )

            options = q.get("options") or shared

            if options:
                keys = [o["key"] for o in options]

                if (
                    len(keys) != len(set(keys))
                    or any(
                        not compact(o.get("text"))
                        for o in options
                    )
                ):
                    errors.append(
                        f"Q{number}: invalid options"
                    )

        # OCR warnings. Do not silently fix source text.
        visible = "\n".join([
            template,
            *[str(q.get("prompt") or "") for q in questions],
            *[str(o.get("text") or "") for o in shared],
            *[
                str(o.get("text") or "")
                for q in questions
                for o in (q.get("options") or [])
            ],
        ])

        if re.search(r"\bAl\b", visible):
            warnings.append(
                f"Q{first}-{last}: possible OCR Al instead of AI"
            )

        if re.search(r"\bofthe\b|[➔→]", visible, re.I):
            warnings.append(
                f"Q{first}-{last}: possible OCR/footer noise"
            )

    if all_slots != expected:
        errors.append(
            "Question slots must cover Q1-Q40 exactly once"
        )

    # Validate grading contracts and their source references.
    numbers_seen = []
    total_marks = 0

    for item in items:
        numbers = item.get("numbers", [])

        if (
            not isinstance(numbers, list)
            or not numbers
            or not all(
                type(n) is int and 1 <= n <= 40
                for n in numbers
            )
        ):
            errors.append(
                f"Invalid answer item numbers: {numbers}"
            )
            continue

        marks = item.get("max_score")
        fmt = item.get("answer_format")
        answer = item.get("correct_answer") or {}
        key = tuple(item.get("source_group", []))
        group = source_groups.get(key)

        if group is None:
            errors.append(
                f"Item {numbers}: unknown source group"
            )
            continue

        expected_type = type_map.get(
            group["question_type_hint"]
        )

        if (
            expected_type is None
            or item.get("question_type") != expected_type
        ):
            errors.append(
                f"Item {numbers}: incorrect DB question_type"
            )

        if any(
            n not in range(key[0], key[1] + 1)
            for n in numbers
        ):
            errors.append(
                f"Item {numbers}: wrong group reference"
            )

        if (
            type(marks) is not int
            or not 1 <= marks <= 5
        ):
            errors.append(
                f"Item {numbers}: invalid max_score"
            )
            continue

        if len(numbers) != marks:
            errors.append(
                f"Item {numbers}: slots/marks mismatch"
            )

        numbers_seen.extend(numbers)
        total_marks += marks

        shared = group.get("shared_options") or []

        reviewed_questions = {
            q["number"]: q
            for q in group.get("questions", [])
        }

        options = shared or (
            reviewed_questions.get(
                numbers[0], {}
            ).get("options") or []
        )

        allowed = {o["key"] for o in options}

        if fmt == "multi_choice":
            choices = answer.get("choices", [])

            if (
                item.get("question_type") != "mcq_multi"
                or len(choices) != marks
                or len(set(choices)) != marks
                or not set(choices).issubset(allowed)
                or item.get("config", {}).get(
                    "select_count"
                ) != marks
            ):
                errors.append(
                    f"Item {numbers}: invalid multi-choice"
                )

            source_answers = {
                str(answer_refs.get(str(n), "")).strip()
                for n in numbers
            }

            if source_answers != set(choices):
                errors.append(
                    f"Item {numbers}: differs from PDF answer key"
                )

        elif fmt == "choice":
            value = answer.get("choice")

            if marks != 1 or value not in allowed:
                errors.append(
                    f"Item {numbers}: invalid single choice"
                )

            if str(
                answer_refs.get(str(numbers[0]), "")
            ).strip() != str(value):
                errors.append(
                    f"Item {numbers}: differs from PDF answer key"
                )

        elif fmt == "text":
            accepted = answer.get("accepted", [])
            config = item.get("config", {})
            limit = config.get("max_words")

            if (
                marks != 1
                or not accepted
                or type(limit) is not int
                or limit < 1
                or any(
                    not isinstance(value, str)
                    or not value.strip()
                    for value in accepted
                )
            ):
                errors.append(
                    f"Item {numbers}: invalid text contract"
                )
            else:
                for value in accepted:
                    tokens = value.split()

                    numeric = sum(
                        bool(re.fullmatch(
                            r"\d+(?:[.,:/]\d+)*",
                            token,
                        ))
                        for token in tokens
                    )

                    if config.get("allow_number"):
                        valid = (
                            len(tokens) - numeric <= limit
                            and numeric <= 1
                        )
                    else:
                        valid = len(tokens) <= limit

                    if not valid:
                        errors.append(
                            f"Item {numbers}: "
                            "answer exceeds word limit"
                        )

        else:
            errors.append(
                f"Item {numbers}: unknown answer format"
            )

        display.extend([
            f"Answer {numbers}: "
            + json.dumps(
                answer,
                ensure_ascii=False,
            ),
            "",
        ])

    # Global invariants.
    if (
        sorted(numbers_seen) != expected
        or len(numbers_seen) != 40
    ):
        errors.append(
            "Answer IR must cover Q1-Q40 exactly once"
        )

    if set(answer_refs) != {
        str(n) for n in expected
    }:
        errors.append(
            "Missing or extra PDF answer reference numbers"
        )

    if total_marks != 40:
        errors.append(
            f"Total score must be 40, got {total_marks}"
        )

    if ir.get("question_slots") != 40:
        errors.append("IR question_slots mismatch")

    if ir.get("question_records") != len(items):
        errors.append("IR question_records mismatch")

    if ir.get("max_score") != total_marks:
        errors.append("IR max_score mismatch")

    report = {
        "status": (
            "BLOCKED" if errors
            else "CONTENT_REVIEW_REQUIRED"
        ),
        "passages": len(passages),
        "groups": len(groups),
        "question_slots": len(all_slots),
        "question_records": len(items),
        "max_score": total_marks,
        "errors": errors,
        "warnings": warnings,
        "manual_pdf_review_required": True,
        "import_ready": False,
    }

    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    (OUT / "qa_report.json").write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (OUT / "qa_review.md").write_text(
        "\n".join(display),
        encoding="utf-8",
    )

    print("\n--- STAGE 5 ANSWER IR QA ---")
    print("Passages:", len(passages))
    print("Groups:", len(groups))
    print("Question slots:", len(all_slots))
    print("Gradable records:", len(items))
    print("Marks:", total_marks)
    print("Errors:", len(errors))
    print("Warnings:", len(warnings))

    for message in errors:
        print(" ERROR:", message)

    for message in warnings:
        print(" WARNING:", message)

    print("Status:", report["status"])
    print(
        "IMPORT BLOCKED - "
        "Finalize and importer not migrated"
    )

def main():
    if Path("output/stage4/answer_ir.json").is_file():
        return validate_ir()
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

    if len(groups) != len(SPEC):
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
