
"""Universal Stage 3: extract Reading question candidates.

Works with the existing Stage-2 structure format.
Reuses the existing parser. Never invents answers.
Does not write to Supabase.
"""

import argparse
import json
import re
from pathlib import Path

from parse_reading_questions import parse_inline, parse_rows


INLINE_TYPES = {
    "NOTE_COMPLETION",
    "SUMMARY_COMPLETION",
    "SUMMARY_WORD_BANK",
    "TABLE_COMPLETION",
    "FLOW_CHART_COMPLETION",
    "FORM_COMPLETION",
    "SENTENCE_COMPLETION",
}

FIXED_OPTIONS = {
    "TRUE_FALSE_NOT_GIVEN": [
        {"key": x, "text": x}
        for x in ("TRUE", "FALSE", "NOT GIVEN")
    ],
    "YES_NO_NOT_GIVEN": [
        {"key": x, "text": x}
        for x in ("YES", "NO", "NOT GIVEN")
    ],
}


def require(condition, message):
    if not condition:
        raise ValueError(message)




def extract_bank(raw, last_question=None):
    """
    Extract A-F / A-I / A-J option banks from PDF text.

    Supports:
      A
      option text

      A option text

    Missing label I can be proposed from a line split,
    but always requires PDF verification.

    Returns:
      options, question_text, bank_text, warnings
    """
    range_match = re.search(
        r"\bA\s*[-–—]\s*([A-J])\b",
        raw,
        re.IGNORECASE,
    )

    if not range_match:
        return None, raw, None, [
            "BANK_RANGE_NOT_FOUND"
        ]

    last_letter = range_match.group(1).upper()

    expected = [
        chr(n)
        for n in range(
            ord("A"),
            ord(last_letter) + 1,
        )
    ]

    lines = raw.splitlines()

    marker = re.compile(
        r"^[ \t]*([A-J])(?:[ \t]+(\S.*))?[ \t]*$"
    )

    # Only consider option banks after the last question.
    # This avoids confusing ordinary text such as
    # "A team of researchers..." with option A.
    cutoff = 0

    if last_question is not None:
        for index, line in enumerate(lines):
            if re.search(
                rf"(?<!\d){last_question}[ \t]*\.{{3,}}",
                line,
            ):
                cutoff = index + 1

            elif re.fullmatch(
                rf"[ \t]*{last_question}[ \t]*[.)]?[ \t]*",
                line,
            ):
                cutoff = index + 1

    candidates = []

    for start in range(cutoff, len(lines)):
        first_match = marker.fullmatch(lines[start])

        if (
            not first_match
            or first_match.group(1) != "A"
        ):
            continue

        located = []

        for position in range(start, len(lines)):
            match = marker.fullmatch(lines[position])

            if (
                match
                and match.group(1) in expected
            ):
                located.append((
                    position,
                    match.group(1),
                    (match.group(2) or "").strip(),
                ))

        letters = [
            item[1]
            for item in located
        ]

        missing = set(expected) - set(letters)

        valid_sequence = (
            bool(letters)
            and letters[0] == "A"
            and letters[-1] == last_letter
            and letters == sorted(set(letters))
            and missing in (set(), {"I"})
        )

        if valid_sequence:
            candidates.append((
                start,
                located,
                missing,
            ))

    if len(candidates) != 1:
        return None, raw, None, [
            f"BANK_ANCHOR_AMBIGUOUS:{len(candidates)}"
        ]

    start, located, missing = candidates[0]

    values = {}
    warnings = []

    for index, (position, letter, initial) in enumerate(
        located
    ):
        stop = (
            located[index + 1][0]
            if index + 1 < len(located)
            else len(lines)
        )

        pieces = (
            [initial] if initial else []
        ) + [
            line.strip()
            for line in lines[position + 1:stop]
            if line.strip()
        ]

        # Remove page numbers and running headers.
        while pieces and re.fullmatch(
            r"(?:\d{1,3}|Test\s*\d+|Reading)",
            pieces[-1],
            re.IGNORECASE,
        ):
            pieces.pop()

        next_letter = (
            located[index + 1][1]
            if index + 1 < len(located)
            else None
        )

        if (
            next_letter
            and ord(next_letter) - ord(letter) == 2
        ):
            candidate = chr(ord(letter) + 1)

            if (
                candidate != "I"
                or len(pieces) < 2
            ):
                return None, raw, None, [
                    f"MISSING_{candidate}_AMBIGUOUS"
                ]

            # A candidate reconstruction, NOT approval.
            values[letter] = " ".join(
                pieces[:-1]
            ).strip()

            values[candidate] = pieces[-1]

            warnings.append(
                "MISSING_PDF_OPTION_LABEL_I: "
                "candidate split requires PDF verification"
            )

        else:
            values[letter] = " ".join(pieces).strip()

    if (
        sorted(values) != expected
        or any(not values[x] for x in expected)
    ):
        return None, raw, None, [
            "BANK_INCOMPLETE_OR_AMBIGUOUS",
            *warnings,
        ]

    options = [
        {
            "key": letter,
            "text": values[letter],
        }
        for letter in expected
    ]

    question_text = "\n".join(
        lines[:start]
    ).rstrip()

    bank_text = "\n".join(
        lines[start:]
    ).strip()

    return (
        options,
        question_text,
        bank_text,
        warnings,
    )


def build_group(source_group, passage_no):
    """
    Universal Stage-3 question group parser.

    Handles:
    - Inline completion
    - Summary word bank
    - Matching sentence endings
    - Multiple choice
    - Multi-choice with multiple marks
    - TFNG / YNNG
    - Other supported row-based types

    Uncertain extraction is always marked NEEDS_REVIEW.
    """
    first = source_group["number_from"]
    last = source_group["number_to"]
    kind = source_group["question_type_hint"]

    raw = "\n".join(
        chunk["raw_text"]
        for chunk in source_group["source_chunks"]
    )

    require(
        bool(raw.strip()),
        f"Q{first}-{last}: empty source",
    )

    warnings = []
    template = None
    bank = None
    shared = None
    questions = []

    # ------------------------------------
    # INLINE COMPLETION
    # ------------------------------------

    if kind in INLINE_TYPES:

        if kind == "SUMMARY_WORD_BANK":
            (
                extracted,
                question_text,
                bank_text,
                bank_warnings,
            ) = extract_bank(
                raw,
                last_question=last,
            )

            (
                questions,
                template,
                _,
                _,
                issues,
            ) = parse_inline(
                source_group,
                question_text,
            )

            shared = extracted
            bank = bank_text

            if extracted:
                issues = [
                    issue
                    for issue in issues
                    if issue not in {
                        "WORD_BANK_START_NOT_FOUND",
                        "WORD_BANK_LAYOUT_REVIEW",
                    }
                ]

            warnings.extend(issues)
            warnings.extend(bank_warnings)

            warnings.append(
                "WORD_BANK_REQUIRES_PDF_VERIFICATION"
            )

        else:
            (
                questions,
                template,
                bank,
                shared,
                issues,
            ) = parse_inline(
                source_group,
                raw,
            )

            warnings.extend(issues)

    # ------------------------------------
    # MATCHING SENTENCE ENDINGS
    # ------------------------------------

    elif kind == "MATCHING_SENTENCE_ENDINGS":
        (
            shared,
            question_text,
            bank,
            bank_warnings,
        ) = extract_bank(
            raw,
            last_question=last,
        )

        (
            questions,
            _,
            _,
            _,
            issues,
        ) = parse_rows(
            source_group,
            question_text,
        )

        warnings.extend(issues)
        warnings.extend(bank_warnings)

        if shared is None:
            warnings.append(
                "MATCHING_ENDINGS_BANK_NOT_PARSED"
            )
        else:
            warnings.append(
                "MATCHING_ENDINGS_BANK_REQUIRES_PDF_VERIFICATION"
            )

    # ------------------------------------
    # MULTIPLE-ANSWER MCQ
    # ------------------------------------

    elif kind in {
        "MCQ_MULTI",
        "MULTIPLE_CHOICE_MULTI",
    }:
        (
            shared,
            question_text,
            bank,
            bank_warnings,
        ) = extract_bank(
            raw,
            last_question=last,
        )

        warnings.extend(bank_warnings)

        select_count = last - first + 1

        require(
            select_count >= 2,
            f"Q{first}-{last}: "
            "multi-choice needs at least two slots",
        )

        questions = [{
            "number": first,
            "numbers": list(
                range(first, last + 1)
            ),
            "question_type_hint": "MCQ_MULTI",
            "question_type": "mcq_multi",
            "answer_format": "multi_choice",
            "max_score": select_count,
            "config": {
                "select_count": select_count
            },
            "render_mode": "QUESTION_CARD",
            "prompt": question_text.strip() or None,
            "options": shared,
            "status": "NEEDS_REVIEW",
        }]

        warnings.append(
            "MCQ_MULTI_REQUIRES_PDF_VERIFICATION"
        )

    # ------------------------------------
    # STANDARD QUESTION ROWS
    # ------------------------------------

    elif kind != "UNKNOWN":
        (
            questions,
            template,
            bank,
            shared,
            issues,
        ) = parse_rows(
            source_group,
            raw,
        )

        warnings.extend(issues)

    else:
        warnings.append(
            "UNKNOWN_QUESTION_TYPE"
        )

    # ------------------------------------
    # FIXED CHOICE OPTIONS
    # ------------------------------------

    if kind in FIXED_OPTIONS:
        shared = [
            dict(option)
            for option in FIXED_OPTIONS[kind]
        ]

    # ------------------------------------
    # FALLBACK: UNPARSED QUESTIONS
    # ------------------------------------

    if not questions:
        questions = [
            {
                "number": number,
                "question_type_hint": kind,
                "render_mode": "QUESTION_CARD",
                "prompt": None,
                "options": None,
                "status": "NEEDS_REVIEW",
            }
            for number in range(first, last + 1)
        ]

    # ------------------------------------
    # VERIFY QUESTION COVERAGE
    # ------------------------------------

    actual_slots = []

    for question in questions:
        numbers = (
            question.get("numbers")
            or [question["number"]]
        )

        actual_slots.extend(numbers)

        if warnings:
            question["status"] = "NEEDS_REVIEW"

        if (
            question.get("render_mode")
            == "QUESTION_CARD"
            and not question.get("prompt")
        ):
            question["status"] = "NEEDS_REVIEW"

    expected_slots = list(
        range(first, last + 1)
    )

    require(
        actual_slots == expected_slots,
        f"Q{first}-{last}: "
        f"incorrect coverage {actual_slots}",
    )

    return {
        "passage_number": passage_no,
        "number_from": first,
        "number_to": last,
        "question_type_hint": kind,
        "source_pages": source_group["source_pages"],
        "raw_group_text": raw,
        "render_template": template,
        "word_bank_raw": bank,
        "shared_options": shared,
        "questions": questions,
        "warnings": sorted(set(warnings)),
    }

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--workspace",
        required=True,
        type=Path,
    )

    args = parser.parse_args()

    stage2 = (
        args.workspace / "output/stage2/structure.json"
    )

    stage3 = (
        args.workspace /
        "output/stage3/question_candidates_v2.json"
    )

    require(
        stage2.is_file(),
        f"Missing Stage 2: {stage2}",
    )

    require(
        not stage3.exists(),
        f"Stage 3 already exists; refusing overwrite: {stage3}",
    )

    structure = json.loads(
        stage2.read_text(encoding="utf-8")
    )

    groups = []

    for passage in structure["passages"]:
        passage_no = passage["passage_number"]

        for group in passage["question_groups"]:
            groups.append(
                build_group(group, passage_no)
            )

    slots = [
        n
        for group in groups
        for question in group["questions"]
        for n in (
            question.get("numbers")
            or [question["number"]]
        )
    ]

    require(
        slots == list(range(1, 41)),
        f"Incorrect coverage: {slots}",
    )

    questions = [
        q
        for g in groups
        for q in g["questions"]
    ]

    review_groups = [
        g
        for g in groups
        if g["warnings"]
        or any(
            q["status"] == "NEEDS_REVIEW"
            for q in g["questions"]
        )
    ]

    result = {
        "status": "CANDIDATES_NOT_IMPORT_READY",
        "groups": groups,
        "question_count": len(questions),
        "question_slots": 40,
        "answers_mapped": False,
    }

    stage3.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    stage3.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("STAGE 3 EXTRACTION COMPLETE")
    print("Groups:", len(groups))
    print("Records:", len(questions))
    print("Slots:", len(slots))
    print("Groups requiring review:", len(review_groups))

    for group in review_groups:
        print(
            f"REVIEW Q{group['number_from']}-"
            f"{group['number_to']}: "
            f"{', '.join(group['warnings']) or 'question content'}"
        )

    print("Output:", stage3)
    print("NOT IMPORT READY")


if __name__ == "__main__":
    main()
