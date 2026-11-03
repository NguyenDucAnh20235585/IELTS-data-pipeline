
import copy
import json
import re
from pathlib import Path

STRUCTURE = Path("output/stage2/structure.json")
MAPPED = Path("output/stage4/reading_test1_mapped.json")
DEST = Path("output/stage6/reading_test1_demo.json")

TITLES = {
    1: "The Davies Sisters",
    2: "Why we need silence",
    3: "Book review: The World of Sugar by",
}

PASSAGE_FIXES = {
    1: {
        "We lmow": "We know",
        "contemporaiy": "contemporary",
        "well-lmown": "well-known",
        "Provenc;al": "Provençal",
        "Franc;ois": "François",
        "·while": "While",
    },
    2: {
        "I-hour float": "1-hour float",
    },
    3: {
        "UlbeBosma": "Ulbe Bosma",
    },
}

INLINE_TYPES = {
    "NOTE_COMPLETION",
    "SUMMARY_COMPLETION",
    "SUMMARY_WORD_BANK",
}

changes = []


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def replace_known(text, fixes, location):
    for old, new in fixes.items():
        count = text.count(old)
        if count:
            text = text.replace(old, new)
            changes.append({
                "location": location,
                "old": old,
                "new": new,
                "count": count,
            })
    return text


def remove_page_noise(text, printed_pages):
    text = re.sub(
        r"(?m)^\s*(?:Test 1|Reading|READING)\s*$",
        "",
        text,
    )

    for number in printed_pages:
        text = re.sub(
            rf"(?m)^[ \t]*{number}[ \t]*$",
            "",
            text,
        )

    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean_passage(passage):
    number = passage["passage_number"]
    pages = passage["source_pages"]

    raw = "\n".join(
        chunk["raw_text"]
        for chunk in passage["body_chunks"]
    )

    title = TITLES[number]
    position = raw.find(title)

    if position < 0:
        raise ValueError(
            f"Passage {number}: title not found"
        )

    content = raw[position:]

    content = replace_known(
        content,
        PASSAGE_FIXES[number],
        f"Passage {number}",
    )

    glossary = []

    if number == 1:
        glossary = [
            {
                "term": "philanthropic",
                "definition": (
                    "seeking to promote the welfare of others, "
                    "often by charitable funding"
                ),
            },
            {
                "term": "Old Master",
                "definition": (
                    "a highly respected artist of great skill "
                    "who worked in Europe before about 1800"
                ),
            },
            {
                "term": "Impressionist",
                "definition": (
                    "an artist with a style of painting that "
                    "developed in France in the late 1800s"
                ),
            },
        ]

        content = re.sub(
            r"(?m)^[ \t]*•{1,2}[ \t]*"
            r"(?:philanthropic|Old Master):[^\n]*\n?",
            "",
            content,
        )

        # Remove corrupted footnote tail from PDF extraction.
        content = re.sub(
            r"(?s)\*\*\* Impressionist:.*$",
            "",
            content,
        )

    content = remove_page_noise(
        content,
        [page - 1 for page in pages],
    )

    return {
        "number": number,
        "title": (
            "Book review: The World of Sugar by Ulbe Bosma"
            if number == 3 else title
        ),
        "content": content,
        "glossary": glossary,
        "source_pages": pages,
    }


def clean_group(group):
    result = copy.deepcopy(group)
    first = result["number_from"]
    last = result["number_to"]
    kind = result["question_type_hint"]

    raw = result.get("raw_group_text") or ""

    if kind in INLINE_TYPES:
        template = result.get("render_template") or ""

        if not template:
            raise ValueError(
                f"Q{first}-{last}: missing template"
            )

        if first == 5 or (first <= 5 <= last):
            template = re.sub(
                r"(\{\{gap:5\}\})[ \t]*\n"
                r"[ \t]*\.{2,}",
                r"\1",
                template,
            )

        if first == 31:
            template = replace_known(
                template,
                {"ofthe": "of the"},
                "Q31-36",
            )

        # Join punctuation accidentally separated from gaps.
        template = re.sub(
            r"(\{\{gap:\d+\}\})[ \t]*\n[ \t]*([,.])",
            r"\1\2",
            template,
        )

        template = remove_page_noise(
            template,
            [page - 1 for page in result["source_pages"]],
        )

        result["render_template"] = template
        result["instructions"] = None

    else:
        match = re.search(
            rf"(?m)^[ \t]*{first}[ \t]*$",
            raw,
        )

        if not match:
            raise ValueError(
                f"Q{first}-{last}: instructions boundary missing"
            )

        result["instructions"] = raw[:match.start()].strip()

    if first == 22:
        options = result.get("shared_options") or []

        for option in options:
            if option["key"] == "D":
                option["text"] = "Eric Pfeifer"
                changes.append({
                    "location": "Q22-26",
                    "old": "Eric pfeifer",
                    "new": "Eric Pfeifer",
                    "count": 1,
                })

    # Preserve only fields intended for display and mapping.
    result.pop("raw_group_text", None)
    result.pop("word_bank_raw", None)

    return result


def validate(data):
    passages = data["passages"]

    if len(passages) != 3:
        raise ValueError("Expected 3 passages")

    groups = [
        group
        for passage in passages
        for group in passage["question_groups"]
    ]

    if len(groups) != 8:
        raise ValueError("Expected 8 groups")

    questions = [
        question
        for group in groups
        for question in group["questions"]
    ]

    numbers = [q["number"] for q in questions]

    if numbers != list(range(1, 41)):
        raise ValueError(
            f"Invalid question numbers: {numbers}"
        )

    for passage in passages:
        if len(passage["content"]) < 1500:
            raise ValueError(
                f"Passage {passage['number']} too short"
            )

    for group in groups:
        template = group.get("render_template") or ""
        kind = group["question_type_hint"]
        shared = group.get("shared_options") or []

        for question in group["questions"]:
            number = question["number"]
            answer = question.get("correct_answer") or {}

            if kind in INLINE_TYPES:
                token = f"{{{{gap:{number}}}}}"

                if template.count(token) != 1:
                    raise ValueError(
                        f"Q{number}: invalid gap"
                    )
            elif not question.get("prompt"):
                raise ValueError(
                    f"Q{number}: missing prompt"
                )

            if question["answer_format"] == "CHOICE":
                options = question.get("options") or shared
                allowed = {o["key"] for o in options}

                if answer.get("choice") not in allowed:
                    raise ValueError(
                        f"Q{number}: invalid choice"
                    )
            elif not answer.get("accepted"):
                raise ValueError(
                    f"Q{number}: missing text answer"
                )

    display_text = "\n".join(
        p["content"] for p in passages
    ) + "\n" + "\n".join(
        g.get("render_template") or ""
        for g in groups
    )

    for artifact in (
        "We lmow",
        "contemporaiy",
        "well-lmown",
        "Provenc;al",
        "Franc;ois",
        "UlbeBosma",
        "ofthe",
    ):
        if artifact in display_text:
            raise ValueError(
                f"Unfixed text artifact: {artifact}"
            )

    return len(groups), len(questions)


def main():
    structure = load(STRUCTURE)
    mapped = load(MAPPED)

    if mapped.get("answers_mapped") is not True:
        raise ValueError("Answer mapping not complete")

    passages = [
        clean_passage(p)
        for p in structure["passages"]
    ]

    for passage in passages:
        number = passage["number"]

        groups = [
            clean_group(group)
            for group in mapped["groups"]
            if group["passage_number"] == number
        ]

        passage["question_groups"] = groups

    result = {
        "test": "Cambridge 21 Reading Test 1",
        "status": "CONTENT_PATCHED_PENDING_PREVIEW",
        "passages": passages,
        "answer_source_page": 119,
    }

    groups_count, questions_count = validate(result)

    DEST.parent.mkdir(parents=True, exist_ok=True)

    DEST.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report_path = DEST.parent / "content_fixes.json"

    report_path.write_text(
        json.dumps(changes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n--- FINAL CONTENT CHECK ---")
    print("Passages:", len(passages))
    print("Groups:", groups_count)
    print("Questions:", questions_count)
    print("Recorded text corrections:", len(changes))
    print("Output:", DEST)
    print("Fix report:", report_path)
    print("\nCONTENT PATCHED - PREVIEW STILL REQUIRED")


if __name__ == "__main__":
    main()
