
import json
import re
from pathlib import Path

PAGE = Path("output/stage1/pages/page_119.json")
QUESTIONS = Path(
    "output/stage3/question_candidates_reviewed.json"
)
OUT = Path("output/stage4")

REFERENCE_VALUES = (
    "mining|education|notes|journals|Venice|canteen|friends|"
    "TRUE|NOT GIVEN|FALSE|NOT GIVEN|TRUE|TRUE|"
    "C|B|A|G|breath|questionnaire|wellbeing|depression|"
    "C|A|B|D|C|B|A|C|A|H|E|I|A|G|C|"
    "YES|NOT GIVEN|NO|YES"
).split("|")


def normalize(value):
    value = re.sub(r"\s+", " ", str(value)).strip().upper()
    return re.sub(r"\bNOT\s*GIVEN\b", "NOT GIVEN", value)


REFERENCE = {
    index: normalize(value)
    for index, value in enumerate(REFERENCE_VALUES, 1)
}

NUMBER_LINE = re.compile(
    r"^([1-9]|[1-3]\d|40)(?:[ \t]+(.+))?$"
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def extract_answers(page):
    lines = [
        line.strip()
        for line in page["text"].splitlines()
        if line.strip()
    ]

    start = next(
        (
            i for i, line in enumerate(lines)
            if line.upper() == "READING"
        ),
        None,
    )

    if start is None:
        raise ValueError("READING heading not found")

        # Giữ lại toàn bộ text sau tiêu đề READING.
    # Thứ tự PDF hai cột có thể khác thứ tự hiển thị.
    # Bộ kiểm tra phía sau sẽ chặn số câu trùng hoặc đáp án sai.
    lines = lines[start + 1:]
    answers = {}
    duplicates = []

    for index, line in enumerate(lines):
        match = NUMBER_LINE.fullmatch(line)

        if match is None:
            continue

        number = int(match.group(1))
        value = (match.group(2) or "").strip()

        if not value and index + 1 < len(lines):
            next_line = lines[index + 1]

            if NUMBER_LINE.fullmatch(next_line) is None:
                value = next_line

        if not value:
            continue

        if number in answers:
            duplicates.append(number)
        else:
            answers[number] = normalize(value)

    return answers, sorted(set(duplicates))


def validate_pdf_answers(answers, duplicates):
    issues = []

    if duplicates:
        issues.append(f"Duplicate answers: {duplicates}")

    missing = sorted(set(range(1, 41)) - set(answers))
    extra = sorted(set(answers) - set(range(1, 41)))

    if missing:
        issues.append(f"Missing answers: {missing}")

    if extra:
        issues.append(f"Unexpected numbers: {extra}")

    for number in range(1, 41):
        actual = answers.get(number)
        expected = REFERENCE[number]

        if actual is not None and actual != expected:
            issues.append(
                f"Q{number}: extracted={actual!r}, "
                f"PDF_reference={expected!r}"
            )

    return issues


def validate_and_map(data, answers):
    issues = []
    all_questions = []

    TEXT_TYPES = {
        "NOTE_COMPLETION",
        "SUMMARY_COMPLETION",
    }

    for group in data["groups"]:
        kind = group["question_type_hint"]
        shared = group.get("shared_options") or []
        template = group.get("render_template") or ""

        if kind == "SUMMARY_WORD_BANK":
            keys = [item["key"] for item in shared]

            if keys != list("ABCDEFGHI"):
                issues.append(
                    "Q31-36: invalid word bank A-I"
                )

        for question in group["questions"]:
            number = question["number"]
            all_questions.append(question)

            if question["status"] not in (
                "EXTRACTED_CANDIDATE",
                "REVIEWED_CANDIDATE",
            ):
                issues.append(
                    f"Q{number}: question is not ready"
                )

            answer = answers[number]

            if kind in TEXT_TYPES:
                if len(answer.split()) > 1:
                    issues.append(
                        f"Q{number}: exceeds one word"
                    )

                question["answer_format"] = "TEXT"
                question["correct_answer"] = {
                    "accepted": [answer.lower()]
                }
                question["config"] = {"max_words": 1}

            else:
                options = (
                    question.get("options") or shared
                )

                allowed = {
                    normalize(item["key"])
                    for item in options
                }

                if not allowed:
                    issues.append(
                        f"Q{number}: missing choice options"
                    )
                elif answer not in allowed:
                    issues.append(
                        f"Q{number}: answer {answer!r} "
                        f"not in {sorted(allowed)}"
                    )

                question["answer_format"] = "CHOICE"
                question["correct_answer"] = {
                    "choice": answer
                }

            if question.get("render_mode") == "INLINE_GAP":
                token = question.get("gap_token")

                if not token or template.count(token) != 1:
                    issues.append(
                        f"Q{number}: invalid gap token"
                    )
            elif not question.get("prompt"):
                issues.append(
                    f"Q{number}: missing prompt"
                )

            question["source_answer_page"] = 119

    numbers = [q["number"] for q in all_questions]

    if numbers != list(range(1, 41)):
        issues.append("Question coverage is not 1-40")

    if len(data["groups"]) != 8:
        issues.append("Expected exactly 8 groups")

    return issues


def main():
    if len(REFERENCE) != 40:
        raise ValueError("Invalid reference answer count")

    if not PAGE.is_file():
        raise FileNotFoundError(PAGE)

    if not QUESTIONS.is_file():
        raise FileNotFoundError(QUESTIONS)

    page = read_json(PAGE)
    data = read_json(QUESTIONS)

    if page.get("pdf_page") != 119:
        raise ValueError("Wrong answer key page")

    OUT.mkdir(parents=True, exist_ok=True)

    answers, duplicates = extract_answers(page)
    extraction_issues = validate_pdf_answers(
        answers, duplicates
    )

    probe = {
        "source_page": 119,
        "extracted_count": len(answers),
        "answers": {
            str(n): answers[n]
            for n in sorted(answers)
        },
        "issues": extraction_issues,
    }

    (OUT / "answer_probe.json").write_text(
        json.dumps(probe, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n--- ANSWER EXTRACTION ---")
    print("Extracted:", len(answers), "/40")
    print("Duplicates:", duplicates)
    print("Extraction issues:", len(extraction_issues))

    for issue in extraction_issues:
        print("  ERROR:", issue)

    if extraction_issues:
        print("\nSTOPPED: PDF answer extraction requires review")
        print("Diagnostic:", OUT / "answer_probe.json")
        raise SystemExit(1)

    mapping_issues = validate_and_map(data, answers)

    print("\n--- ANSWER MAPPING ---")
    print("Question records:", sum(
        len(g["questions"]) for g in data["groups"]
    ))
    print("Mapping issues:", len(mapping_issues))

    for issue in mapping_issues:
        print("  ERROR:", issue)

    if mapping_issues:
        print("\nSTOPPED: Answer mapping requires review")
        raise SystemExit(1)

    data["status"] = "ANSWER_MAPPED_QA_PENDING"
    data["answers_mapped"] = True
    data["answer_source_pdf_page"] = 119

    destination = OUT / "reading_test1_mapped.json"

    destination.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n--- STAGE 4 SUMMARY ---")
    print("Groups:", len(data["groups"]))
    print("Answers:", len(answers))
    print("Reference mismatches: 0")
    print("Mapping issues: 0")
    print("Output:", destination)
    print("\nSTAGE 4 PASS - CONTENT QA STILL REQUIRED")


if __name__ == "__main__":
    main()
