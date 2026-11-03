
import json
import re
from collections import Counter
from pathlib import Path

SOURCE = Path("output/stage2/structure.json")
OUTPUT = Path("output/stage3/question_candidates_v2.json")

INLINE_TYPES = {
    "NOTE_COMPLETION",
    "SUMMARY_COMPLETION",
    "SUMMARY_WORD_BANK",
}

GAP_RE = re.compile(
    r"(?<!\d)(?P<n>\d{1,2})[ \t]*"
    r"(?:\.(?:[ \t]*\.){2,}|_{3,}|…{2,})"
)

NUMBER_RE = re.compile(
    r"^([1-9]|[1-3]\d|40)(?:[.)])?"
    r"(?:\s+(.+))?$"
)

LETTER_RE = re.compile(r"^[A-I]$")


def compact(value):
    return re.sub(r"\s+", " ", value).strip()


def clean_lines(lines):
    result = []

    for line in lines:
        value = line.strip()

        if value.startswith(("➔", "→")):
            break

        if value:
            result.append(value)

    while result and re.fullmatch(r"\d{1,3}", result[-1]):
        result.pop()

    return result


def split_questions(lines, first, last):
    expected = list(range(first, last + 1))
    starts = []
    target_index = 0

    for index, line in enumerate(lines):
        match = NUMBER_RE.fullmatch(line.strip())

        if not match or target_index >= len(expected):
            continue

        number = int(match.group(1))

        if number != expected[target_index]:
            continue

        starts.append({
            "number": number,
            "line_index": index,
            "inline_text": match.group(2) or "",
        })

        target_index += 1

    if len(starts) != len(expected):
        return None, (
            f"ROW_MISMATCH expected={expected}, "
            f"found={[s['number'] for s in starts]}"
        )

    result = []

    for i, start in enumerate(starts):
        end = (
            starts[i + 1]["line_index"]
            if i + 1 < len(starts)
            else len(lines)
        )

        content = []

        if start["inline_text"]:
            content.append(start["inline_text"])

        content.extend(
            lines[start["line_index"] + 1:end]
        )

        result.append({
            "number": start["number"],
            "lines": clean_lines(content),
        })

    return result, None


def extract_letter_options(lines, letters):
    positions = []

    for index, line in enumerate(lines):
        value = line.strip()

        if LETTER_RE.fullmatch(value):
            positions.append((index, value))

    if [letter for _, letter in positions] != list(letters):
        return None, compact("\n".join(lines))

    first_position = positions[0][0]
    prompt = compact("\n".join(lines[:first_position]))
    options = []

    for i, (index, letter) in enumerate(positions):
        end = (
            positions[i + 1][0]
            if i + 1 < len(positions)
            else len(lines)
        )

        value = compact("\n".join(lines[index + 1:end]))

        if not value:
            return None, prompt

        options.append({
            "key": letter,
            "text": value,
        })

    return options, prompt


def parse_inline(group, raw):
    first = group["number_from"]
    last = group["number_to"]
    kind = group["question_type_hint"]
    expected = list(range(first, last + 1))

    matches = [
        m for m in GAP_RE.finditer(raw)
        if first <= int(m.group("n")) <= last
    ]

    found = [int(m.group("n")) for m in matches]
    counts = Counter(found)
    warnings = []

    if sorted(found) != expected:
        warnings.append(
            f"GAP_MISMATCH expected={expected} found={found}"
        )

    template_source = raw
    word_bank_raw = None

    if kind == "SUMMARY_WORD_BANK":
        if matches:
            after_last_gap = max(m.end() for m in matches)

            bank_start = re.search(
                r"(?m)^[ \t]*A[ \t]*$",
                raw[after_last_gap:],
            )

            if bank_start:
                cut = after_last_gap + bank_start.start()
                template_source = raw[:cut]
                word_bank_raw = raw[cut:].strip()
            else:
                warnings.append("WORD_BANK_START_NOT_FOUND")

        warnings.append("WORD_BANK_LAYOUT_REVIEW")

    def replace_gap(match):
        number = int(match.group("n"))

        if first <= number <= last:
            return "{{gap:" + str(number) + "}}"

        return match.group(0)

    template = GAP_RE.sub(replace_gap, template_source)

    questions = []

    for number in expected:
        valid = counts[number] == 1

        questions.append({
            "number": number,
            "question_type_hint": kind,
            "render_mode": "INLINE_GAP",
            "gap_token": f"{{{{gap:{number}}}}}",
            "prompt": None,
            "options": None,
            "status": (
                "EXTRACTED_CANDIDATE"
                if valid and kind != "SUMMARY_WORD_BANK"
                else "NEEDS_REVIEW"
            ),
        })

    return questions, template, word_bank_raw, None, warnings


def parse_rows(group, raw):
    first = group["number_from"]
    last = group["number_to"]
    kind = group["question_type_hint"]

    warnings = []
    shared_options = None
    question_text = raw
    bank_text = None

    if kind == "MATCHING_FEATURES":
        match = re.search(
            r"(?im)^[ \t]*List[ \t]+of[ \t]+People[ \t]*$",
            raw,
        )

        if match:
            question_text = raw[:match.start()]
            bank_text = raw[match.end():]

            shared_options, _ = extract_letter_options(
                clean_lines(bank_text.splitlines()),
                "ABCD",
            )

            if shared_options is None:
                warnings.append("MATCHING_BANK_REVIEW")
        else:
            warnings.append("MATCHING_BANK_NOT_FOUND")

    elif kind == "MATCHING_INFORMATION":
        shared_options = [
            {"key": c, "text": c}
            for c in "ABCDEFG"
        ]

    elif kind == "TRUE_FALSE_NOT_GIVEN":
        shared_options = [
            {"key": c, "text": c}
            for c in ("TRUE", "FALSE", "NOT GIVEN")
        ]

    elif kind == "YES_NO_NOT_GIVEN":
        shared_options = [
            {"key": c, "text": c}
            for c in ("YES", "NO", "NOT GIVEN")
        ]

    lines = question_text.splitlines()
    rows, error = split_questions(lines, first, last)

    if error:
        warnings.append(error)
        rows = [
            {"number": n, "lines": []}
            for n in range(first, last + 1)
        ]

    questions = []

    for row in rows:
        prompt = compact("\n".join(row["lines"]))
        options = None

        if kind == "MULTIPLE_CHOICE":
            options, prompt = extract_letter_options(
                row["lines"],
                "ABCD",
            )

            if options is None:
                warnings.append(
                    f"Q{row['number']}: MCQ_OPTIONS_REVIEW"
                )

        valid = bool(prompt)

        if kind == "MULTIPLE_CHOICE":
            valid = valid and options is not None

        if kind == "MATCHING_FEATURES":
            valid = valid and shared_options is not None

        questions.append({
            "number": row["number"],
            "question_type_hint": kind,
            "render_mode": "QUESTION_CARD",
            "prompt": prompt or None,
            "options": options,
            "status": (
                "EXTRACTED_CANDIDATE"
                if valid else "NEEDS_REVIEW"
            ),
        })

    return questions, None, bank_text, shared_options, warnings


def main():
    data = json.loads(
        SOURCE.read_text(encoding="utf-8")
    )

    groups = []

    for passage in data["passages"]:
        for source_group in passage["question_groups"]:
            raw = "\n".join(
                chunk["raw_text"]
                for chunk in source_group["source_chunks"]
            )

            kind = source_group["question_type_hint"]

            if kind in INLINE_TYPES:
                parsed = parse_inline(source_group, raw)
            else:
                parsed = parse_rows(source_group, raw)

            questions, template, bank, shared, warnings = parsed

            groups.append({
                "passage_number": passage["passage_number"],
                "number_from": source_group["number_from"],
                "number_to": source_group["number_to"],
                "question_type_hint": kind,
                "source_pages": source_group["source_pages"],
                "raw_group_text": raw,
                "render_template": template,
                "word_bank_raw": bank,
                "shared_options": shared,
                "questions": questions,
                "warnings": warnings,
            })

    all_questions = [
        q for group in groups
        for q in group["questions"]
    ]

    numbers = [q["number"] for q in all_questions]

    if numbers != list(range(1, 41)):
        raise ValueError(
            f"Invalid question coverage: {numbers}"
        )

    if len(groups) != 8:
        raise ValueError(
            f"Expected 8 groups, found {len(groups)}"
        )

    result = {
        "status": "CANDIDATES_NOT_IMPORT_READY",
        "groups": groups,
        "question_count": len(all_questions),
        "answers_mapped": False,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n--- STAGE 3 V2 ---")

    for group in groups:
        counts = Counter(
            q["status"]
            for q in group["questions"]
        )

        print(
            f"Q{group['number_from']:02d}-"
            f"{group['number_to']:02d} | "
            f"candidates={counts['EXTRACTED_CANDIDATE']} | "
            f"review={counts['NEEDS_REVIEW']}"
        )

        for issue in group["warnings"]:
            print("  WARNING:", issue)

    counts = Counter(
        q["status"] for q in all_questions
    )

    print("\n--- SUMMARY ---")
    print("Groups:", len(groups))
    print("Questions:", len(all_questions))
    print("Candidates:", counts["EXTRACTED_CANDIDATE"])
    print("Needs review:", counts["NEEDS_REVIEW"])
    print("Output:", OUTPUT)
    print("\nNOT IMPORT READY")


if __name__ == "__main__":
    main()
