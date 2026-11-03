
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "configs/cambridge21_reading_test1.json"

FILES = {
    "parser": "pipeline/parse_reading_questions.py",
    "mapper": "pipeline/map_answer_keys.py",
    "review": "review/apply_question_reviews.py",
    "qa": "review/validate_reading_dataset.py",
    "finalizer": "review/finalize_reading_dataset.py",
    "importer": "importers/import_cambridge21_reading_test1.py",
    "runner": "run_pipeline_v2.py",
}


def once(text, old, new, expected=1):
    count = text.count(old)
    if count != expected:
        raise ValueError(
            f"Expected {expected} occurrences, got {count}: "
            f"{old[:75]!r}"
        )
    return text.replace(old, new)


def section(text, start, end, replacement):
    if text.count(start) != 1:
        raise ValueError(f"Invalid start anchor: {start}")

    a = text.index(start)
    b = text.index(end, a + len(start))

    return text[:a] + replacement + text[b:]


def change_parser(text):
    text = once(
        text,
        "INLINE_TYPES = {",
        "CHOICE_KEYS = {}\nEXPECTED_GROUP_COUNT = 8\n\n"
        "INLINE_TYPES = {",
    )

    text = once(
        text,
        '                "ABCD",',
        '                CHOICE_KEYS.get(kind, list("ABCD")),',
        expected=2,
    )

    text = once(
        text,
        'for c in "ABCDEFG"',
        'for c in CHOICE_KEYS.get(kind, list("ABCDEFG"))',
    )

    text = once(
        text,
        'for c in ("TRUE", "FALSE", "NOT GIVEN")',
        'for c in CHOICE_KEYS.get('
        'kind, ["TRUE", "FALSE", "NOT GIVEN"])',
    )

    text = once(
        text,
        'for c in ("YES", "NO", "NOT GIVEN")',
        'for c in CHOICE_KEYS.get('
        'kind, ["YES", "NO", "NOT GIVEN"])',
    )

    return once(
        text,
        "if len(groups) != 8:",
        "if len(groups) != EXPECTED_GROUP_COUNT:",
    )


def change_mapper(text):
    text = section(
        text,
        "REFERENCE_VALUES = (",
        "NUMBER_LINE = re.compile(",
        "REFERENCE = {}\n"
        "ANSWER_PAGE = 119\n"
        "EXPECTED_GROUP_COUNT = 8\n\n",
    )

    replacements = {
        'page.get("pdf_page") != 119':
            'page.get("pdf_page") != ANSWER_PAGE',
        'question["source_answer_page"] = 119':
            'question["source_answer_page"] = ANSWER_PAGE',
        '"source_page": 119,':
            '"source_page": ANSWER_PAGE,',
        'data["answer_source_pdf_page"] = 119':
            'data["answer_source_pdf_page"] = ANSWER_PAGE',
        'if len(data["groups"]) != 8:':
            'if len(data["groups"]) != EXPECTED_GROUP_COUNT:',
    }

    for old, new in replacements.items():
        text = once(text, old, new)

    return text


def change_review(text):
    text = once(
        text,
        "SOURCE = Path(",
        "EXPECTED_GROUP_COUNT = 8\n\nSOURCE = Path(",
    )
    return once(
        text,
        'len(data["groups"]) != 8',
        'len(data["groups"]) != EXPECTED_GROUP_COUNT',
    )


def change_qa(text):
    text = section(
        text,
        "CHOICES = {",
        "def load(path):",
        "CHOICES = {}\n\n\n",
    )

    return once(
        text,
        "if len(groups) != 8:",
        "if len(groups) != len(SPEC):",
    )


def change_finalizer(text):
    text = section(
        text,
        "TITLES = {",
        "INLINE_TYPES = {",
        """
TITLES = {}
PASSAGE_FIXES = {}
DISPLAY_TITLES = {}
PASSAGE_GLOSSARY = {}
FOOTER_CUT_MARKERS = {}
GROUP_TEXT_FIXES = {}
GROUP_OPTION_FIXES = {}
TEST_TITLE = ""
ANSWER_PAGE = 0
EXPECTED_GROUP_COUNT = 8


""",
    )

    text = section(
        text,
        "    glossary = []",
        "    content = remove_page_noise(",
        """    glossary = copy.deepcopy(
        PASSAGE_GLOSSARY.get(number, [])
    )

    for entry in glossary:
        term = re.escape(entry["term"])
        content = re.sub(
            rf"(?m)^[ \\\\t]*•{{1,2}}[ \\\\t]*{term}:[^\\\\n]*\\\\n?",
            "",
            content,
        )

    marker = FOOTER_CUT_MARKERS.get(number)
    if marker:
        index = content.find(marker)
        if index >= 0:
            content = content[:index]

""".replace("\\\\", "\\"),
    )

    text = once(
        text,
        '''"title": (
            "Book review: The World of Sugar by Ulbe Bosma"
            if number == 3 else title
        ),''',
        '"title": DISPLAY_TITLES.get(number, title),',
    )

    text = section(
        text,
        "        if first == 5 or (first <= 5 <= last):",
        "        # Join punctuation accidentally separated from gaps.",
        r'''        # Repair accidental PDF line wrapping after a gap.
        template = re.sub(
            r"(\{\{gap:\d+\}\})[ \t]*\n[ \t]*\.{2,}",
            r"\1",
            template,
        )

        template = replace_known(
            template,
            GROUP_TEXT_FIXES.get(f"{first}-{last}", {}),
            f"Q{first}-{last}",
        )

''',
    )

    text = section(
        text,
        "    if first == 22:",
        "    # Preserve only fields intended for display and mapping.",
        '''    option_fixes = GROUP_OPTION_FIXES.get(
        f"{first}-{last}", {}
    )

    for option in result.get("shared_options") or []:
        key = option["key"]
        if key not in option_fixes:
            continue

        old = option["text"]
        new = option_fixes[key]

        if old != new:
            option["text"] = new
            changes.append({
                "location": f"Q{first}-{last}",
                "old": old,
                "new": new,
                "count": 1,
            })

''',
    )

    text = once(
        text,
        'if len(groups) != 8:',
        'if len(groups) != EXPECTED_GROUP_COUNT:',
    )
    text = once(
        text,
        'r"(?m)^\\s*(?:Test 1|Reading|READING)\\s*$"',
        'r"(?m)^\\s*(?:Test\\s+\\d+|Reading|READING)\\s*$"',
    )
    text = once(
        text,
        '"test": "Cambridge 21 Reading Test 1",',
        '"test": TEST_TITLE,',
    )
    text = once(
        text,
        '"answer_source_page": 119,',
        '"answer_source_page": ANSWER_PAGE,',
    )

    return text


def change_importer(text):
    text = once(
        text,
        "INLINE_TYPES = {",
        "EXPECTED_GROUP_COUNT = 8\n\nINLINE_TYPES = {",
    )
    text = once(
        text,
        'if sum(len(p["groups"]) for p in result) != 8:',
        'if sum(len(p["groups"]) for p in result) '
        '!= EXPECTED_GROUP_COUNT:',
    )
    return once(
        text,
        "if section_count != 3 or group_count != 8:",
        "if section_count != 3 or "
        "group_count != EXPECTED_GROUP_COUNT:",
    )


def change_runner(text):
    text = once(
        text,
        '    elif command == "review":',
        '''    elif command == "questions":
        module.CHOICE_KEYS = {
            kind: list(keys)
            for kind, keys in config["choice_keys"].items()
        }
        module.EXPECTED_GROUP_COUNT = len(config["groups"])

    elif command == "review":
        module.EXPECTED_GROUP_COUNT = len(config["groups"])''',
    )

    text = once(
        text,
        '    elif command == "answers":\n'
        '        page = config["answer_page"]',
        '    elif command == "answers":\n'
        '        module.ANSWER_PAGE = config["answer_page"]\n'
        '        module.EXPECTED_GROUP_COUNT = len(config["groups"])\n'
        '        page = config["answer_page"]',
    )

    text = once(
        text,
        '    elif command == "qa":\n'
        '        module.SPEC = [',
        '    elif command == "qa":\n'
        '        module.CHOICES = {\n'
        '            kind: list(keys)\n'
        '            for kind, keys in config["choice_keys"].items()\n'
        '        }\n'
        '        module.SPEC = [',
    )

    text = once(
        text,
        '    elif command == "finalize":\n'
        '        module.TITLES = {',
        '''    elif command == "finalize":
        module.EXPECTED_GROUP_COUNT = len(config["groups"])
        module.TEST_TITLE = config["title"]
        module.ANSWER_PAGE = config["answer_page"]

        module.DISPLAY_TITLES = {
            int(k): v
            for k, v in config["passage_display_titles"].items()
        }
        module.PASSAGE_GLOSSARY = {
            int(k): v
            for k, v in config["glossary"].items()
        }
        module.FOOTER_CUT_MARKERS = {
            int(k): v
            for k, v in config["footer_cut_markers"].items()
        }
        module.GROUP_TEXT_FIXES = config["group_text_fixes"]
        module.GROUP_OPTION_FIXES = config["group_option_fixes"]

        module.TITLES = {''',
    )

    text = once(
        text,
        '    elif command == "import":\n'
        '        module.TITLE = config["title"]',
        '    elif command == "import":\n'
        '        module.EXPECTED_GROUP_COUNT = len(config["groups"])\n'
        '        module.TITLE = config["title"]',
    )

    return text


def update_config(data):
    data["choice_keys"] = {
        "TRUE_FALSE_NOT_GIVEN": [
            "TRUE", "FALSE", "NOT GIVEN"
        ],
        "YES_NO_NOT_GIVEN": [
            "YES", "NO", "NOT GIVEN"
        ],
        "MATCHING_INFORMATION": list("ABCDEFG"),
        "MATCHING_FEATURES": list("ABCD"),
        "SUMMARY_WORD_BANK": list("ABCDEFGHI"),
        "MULTIPLE_CHOICE": list("ABCD"),
    }

    data["passage_display_titles"] = {
        "3": "Book review: The World of Sugar by Ulbe Bosma"
    }

    data["glossary"] = {
        "1": [
            {
                "term": "philanthropic",
                "definition":
                    "seeking to promote the welfare of others, "
                    "often by charitable funding",
            },
            {
                "term": "Old Master",
                "definition":
                    "a highly respected artist of great skill "
                    "who worked in Europe before about 1800",
            },
            {
                "term": "Impressionist",
                "definition":
                    "an artist with a style of painting that "
                    "developed in France in the late 1800s",
            },
        ]
    }

    data["footer_cut_markers"] = {
        "1": "*** Impressionist"
    }

    data["group_text_fixes"] = {
        "31-36": {"ofthe": "of the"}
    }

    data["group_option_fixes"] = {
        "22-26": {"D": "Eric Pfeifer"}
    }

    return data


def main():
    if not CONFIG.is_file():
        raise FileNotFoundError(CONFIG)

    originals = {}
    updated = {}

    for name, relative in FILES.items():
        path = ROOT / relative
        originals[path] = path.read_text(encoding="utf-8-sig")

    originals[CONFIG] = CONFIG.read_text(encoding="utf-8-sig")

    transforms = {
        "parser": change_parser,
        "mapper": change_mapper,
        "review": change_review,
        "qa": change_qa,
        "finalizer": change_finalizer,
        "importer": change_importer,
        "runner": change_runner,
    }

    # Prepare every edit in memory before touching files.
    for name, relative in FILES.items():
        path = ROOT / relative
        new_text = transforms[name](originals[path])
        compile(new_text, str(path), "exec")
        updated[path] = new_text

    config_data = update_config(
        json.loads(originals[CONFIG])
    )

    updated[CONFIG] = json.dumps(
        config_data, ensure_ascii=False, indent=2
    ) + "\n"

    backup = ROOT / (
        "_backup_before_v3_" +
        datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    for path in originals:
        target = backup / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    try:
        for path, content in updated.items():
            path.write_text(content, encoding="utf-8")
    except Exception:
        for path, content in originals.items():
            path.write_text(content, encoding="utf-8")
        raise

    print("\n--- ENGINE V3 REFACTOR ---")
    print("Files updated:", len(updated))
    print("Backup:", backup)
    print("Python syntax: PASS")
    print("Database: NOT TOUCHED")
    print("Git: NOT TOUCHED")
    print("Next: run offline regression")


if __name__ == "__main__":
    main()
