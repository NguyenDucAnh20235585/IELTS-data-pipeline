
"""Universal IELTS Reading Stage 4 answer-key mapping.

Inputs:
  output/stage1/manifest.json
  output/stage1/pages/page_NNN.json
  output/stage3/question_candidates_v2.json

Outputs:
  output/stage4/answer_probe.json
  output/stage4/reading_mapped.json

No database operations. Never overwrites Stage 4.
"""

import argparse
import copy
import json
import re
from pathlib import Path


NUMBER = re.compile(
    r"^(?P<n>[1-9]|[1-3]\d|40)"
    r"(?:[.)])?(?:\s+(?P<answer>.+))?$"
)

MULTI = re.compile(
    r"^([1-9]|[1-3]\d|40)\s*"
    r"(?:&|and)\s*([1-9]|[1-3]\d|40)$",
    re.I,
)

PARENTHETICAL = re.compile(r"^\(([^()]+)\)$")
LETTER = re.compile(r"^[A-Z]$")

TEXT_TYPES = {
    "NOTE_COMPLETION",
    "SUMMARY_COMPLETION",
    "TABLE_COMPLETION",
    "FLOW_CHART_COMPLETION",
    "FORM_COMPLETION",
    "SENTENCE_COMPLETION",
}

FIXED = {
    "TRUE_FALSE_NOT_GIVEN": {
        "TRUE", "FALSE", "NOT GIVEN"
    },
    "YES_NO_NOT_GIVEN": {
        "YES", "NO", "NOT GIVEN"
    },
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load(path):
    require(
        path.is_file(),
        f"Missing input: {path}",
    )

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def normalized(value):
    value = re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()

    return re.sub(
        r"(?i)\bNOT\s*GIVEN\b",
        "NOT GIVEN",
        value,
    )


def extract_answers(text):
    """Read numbered answers from the PDF answer-key text."""

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    require(
        any(
            line.upper() == "READING"
            for line in lines[:16]
        ),
        "Expected READING answer-key heading",
    )

    answers = {}
    unparsed = []

    for index, line in enumerate(lines):

        # Handle combined answers such as Q20&21.
        pair = MULTI.fullmatch(line)

        if pair:
            first = int(pair.group(1))
            second = int(pair.group(2))

            require(
                second == first + 1,
                f"Invalid combined key: {line}",
            )

            following = [
                value
                for value in lines[index + 1:index + 7]
                if value.upper() not in {
                    "IN EITHER ORDER",
                    "EITHER ORDER",
                }
            ]

            require(
                len(following) >= 2
                and all(
                    LETTER.fullmatch(value)
                    for value in following[:2]
                ),
                f"Combined key missing answers: {line}",
            )

            for number, choice in zip(
                (first, second),
                following[:2],
            ):
                require(
                    number not in answers,
                    f"Duplicate Q{number}",
                )

                answers[number] = choice

            continue

        match = NUMBER.fullmatch(line)

        if not match:
            continue

        number = int(match.group("n"))
        raw = (match.group("answer") or "").strip()
        value_index = index

        if not raw:
            if index + 1 >= len(lines):
                continue

            raw = lines[index + 1]
            value_index = index + 1

        invalid_heading = re.match(
            r"(?i)^(?:READING|READING PASSAGE|"
            r"QUESTIONS|IF YOU SCORE|TEST\s+\d+)",
            raw,
        )

        if NUMBER.fullmatch(raw) or invalid_heading:
            unparsed.append(
                f"Q{number}: invalid answer {raw!r}"
            )
            continue

        # Preserve optional parenthetical wording.
        if value_index + 1 < len(lines):
            optional = PARENTHETICAL.fullmatch(
                lines[value_index + 1]
            )

            if optional:
                raw += " " + lines[value_index + 1]

        require(
            number not in answers,
            f"Duplicate answer Q{number}",
        )

        answers[number] = normalized(raw)

    require(
        not unparsed,
        "\n".join(unparsed),
    )

    missing = sorted(
        set(range(1, 41)) - answers.keys()
    )

    require(
        not missing,
        f"Answer key missing Q{missing}",
    )

    return answers


def accepted_text(raw, number, review_flags):
    """Preserve accepted text variants without hiding uncertainty."""

    raw = normalized(raw)

    optional = re.fullmatch(
        r"(.+?)\s*\(([^()]+)\)",
        raw,
    )

    if optional:
        base = optional.group(1)
        addition = optional.group(2)

        review_flags.append(
            f"Q{number}: parenthetical answer "
            "requires PDF review"
        )

        return list(dict.fromkeys([
            base.lower(),
            f"{base} {addition}".lower(),
        ]))

    if (
        " | " in raw
        or " / " in raw
        or " I " in raw
    ):
        if " | " in raw:
            separator = r"\s+\|\s+"
        elif " / " in raw:
            separator = r"\s+/\s+"
        else:
            separator = r"\s+I\s+"

        alternatives = [
            part.strip().lower()
            for part in re.split(separator, raw)
        ]

        require(
            all(alternatives),
            f"Q{number}: empty answer alternative",
        )

        review_flags.append(
            f"Q{number}: answer alternatives "
            "require PDF review"
        )

        return list(dict.fromkeys(alternatives))

    return [raw.lower()]


def map_answers(stage3, answers, page_number):
    """Map answers while preserving Stage 3 review status."""

    result = copy.deepcopy(stage3)

    groups = result.get("groups")

    require(
        isinstance(groups, list) and bool(groups),
        "No Stage 3 question groups",
    )

    used_slots = []
    review_flags = []

    for group in groups:
        first = group["number_from"]
        last = group["number_to"]
        kind = group["question_type_hint"]

        questions = group["questions"]

        require(
            bool(questions),
            f"Q{first}-{last}: no question records",
        )

        if group.get("warnings"):
            review_flags.append(
                f"Q{first}-{last}: "
                "Stage 3 warnings remain"
            )

        for question in questions:
            number = question["number"]

            slots = (
                question.get("numbers")
                or [number]
            )

            require(
                slots == list(
                    range(slots[0], slots[-1] + 1)
                ),
                f"Q{number}: invalid slots",
            )

            require(
                all(
                    first <= n <= last
                    for n in slots
                ),
                f"Q{number}: slots outside group",
            )

            used_slots.extend(slots)

            if question.get("status") not in {
                "EXTRACTED_CANDIDATE",
                "REVIEWED_CANDIDATE",
            }:
                review_flags.append(
                    f"Q{number}: candidate needs review"
                )

            # Multi-answer MCQ.
            if len(slots) > 1:

                require(
                    kind in {
                        "MCQ_MULTI",
                        "MULTIPLE_CHOICE_MULTI",
                    },
                    f"Q{number}: invalid multi-slot type",
                )

                choices = [
                    normalized(answers[n]).upper()
                    for n in slots
                ]

                require(
                    len(set(choices)) == len(choices),
                    f"Q{number}: duplicate answer choices",
                )

                options = (
                    question.get("options")
                    or group.get("shared_options")
                    or []
                )

                allowed = {
                    str(option["key"]).upper()
                    for option in options
                }

                require(
                    bool(allowed)
                    and set(choices) <= allowed,
                    f"Q{number}: answers {choices} "
                    f"outside {sorted(allowed)}",
                )

                question["question_type"] = "mcq_multi"
                question["answer_format"] = "MULTI_CHOICE"
                question["correct_answer"] = {
                    "choices": choices
                }
                question["max_score"] = len(slots)
                question["config"] = {
                    **(question.get("config") or {}),
                    "select_count": len(slots),
                }

            # Text completion.
            elif kind in TEXT_TYPES:

                question["answer_format"] = "TEXT"
                question["correct_answer"] = {
                    "accepted": accepted_text(
                        answers[number],
                        number,
                        review_flags,
                    )
                }

            # Single-choice questions.
            else:

                answer = normalized(
                    answers[number]
                ).upper()

                require(
                    LETTER.fullmatch(answer)
                    or answer in FIXED.get(kind, set()),
                    f"Q{number}: invalid choice {answer!r}",
                )

                if kind in FIXED:
                    allowed = FIXED[kind]
                else:
                    options = (
                        question.get("options")
                        or group.get("shared_options")
                        or []
                    )

                    allowed = {
                        str(option["key"]).upper()
                        for option in options
                    }

                require(
                    bool(allowed)
                    and answer in allowed,
                    f"Q{number}: answer {answer!r} "
                    f"not in {sorted(allowed)}",
                )

                question["answer_format"] = "CHOICE"
                question["correct_answer"] = {
                    "choice": answer
                }

            question["source_answer_page"] = page_number

    require(
        used_slots == list(range(1, 41)),
        f"Invalid Stage 3 coverage: {used_slots}",
    )

    result["status"] = (
        "ANSWER_MAPPED_CONTENT_REVIEW_REQUIRED"
    )
    result["answers_mapped"] = True
    result["answer_source_pdf_page"] = page_number
    result["manual_review_required"] = True
    result["review_flags"] = sorted(
        set(review_flags)
    )

    return result


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
    )

    args = parser.parse_args()

    output = args.workspace / "output"

    manifest = load(
        output / "stage1/manifest.json"
    )

    test_number = manifest.get("test_number")
    page_number = manifest.get("answer_page")

    require(
        isinstance(page_number, int)
        and page_number > 0,
        "Stage 1 manifest lacks answer_page",
    )

    page = load(
        output
        / "stage1/pages"
        / f"page_{page_number:03d}.json"
    )

    require(
        page.get("pdf_page") == page_number,
        "Answer key page mismatch",
    )

    stage3 = load(
        output
        / "stage3/question_candidates_v2.json"
    )

    stage4 = output / "stage4"

    require(
        not stage4.exists(),
        f"Stage 4 exists; refusing overwrite: {stage4}",
    )

    raw_answers = extract_answers(
        page["text"]
    )

    mapped = map_answers(
        stage3,
        raw_answers,
        page_number,
    )

    probe = {
        "test_number": test_number,
        "answer_page": page_number,
        "answers": {
            str(n): raw_answers[n]
            for n in range(1, 41)
        },
        "count": 40,
        "source": "Stage 1 PDF text",
        "manual_review_required": True,
    }

    stage4.mkdir(
        parents=True,
        exist_ok=False,
    )

    (stage4 / "answer_probe.json").write_text(
        json.dumps(
            probe,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (stage4 / "reading_mapped.json").write_text(
        json.dumps(
            mapped,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    records = sum(
        len(g["questions"])
        for g in mapped["groups"]
    )

    print(
        f"TEST: {test_number}; "
        f"ANSWER KEY PAGE: {page_number}"
    )
    print(
        f"KEYS: 40; "
        f"GROUPS: {len(mapped['groups'])}; "
        f"RECORDS: {records}"
    )
    print(
        "REVIEW FLAGS:",
        len(mapped["review_flags"]),
    )

    for flag in mapped["review_flags"]:
        print(" -", flag)

    print(
        "STAGE 4 MAPPING PASS - "
        "CONTENT REVIEW REQUIRED - "
        "NO DB WRITES"
    )


if __name__ == "__main__":
    main()
