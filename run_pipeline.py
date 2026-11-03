
"""IELTS data pipeline runner with isolated workspaces."""

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent

DEFAULT_CONFIG = "configs/cambridge21_reading_test1.json"

SCRIPTS = {
    "extract": "pipeline/extract_pdf_layout.py",
    "structure": "pipeline/detect_reading_structure.py",
    "questions": "pipeline/parse_reading_questions.py",
    "review": "review/apply_question_reviews.py",
    "answers": "pipeline/map_answer_keys.py",
    "qa": "review/validate_reading_dataset.py",
    "finalize": "review/finalize_reading_dataset.py",
    "import": "importers/import_cambridge21_reading_test1.py",
}

ORDER = [
    "extract",
    "structure",
    "questions",
    "review",
    "answers",
    "qa",
    "finalize",
]

OUTPUTS = {
    "extract": "output/stage1/manifest.json",
    "structure": "output/stage2/structure.json",
    "questions": "output/stage3/question_candidates_v2.json",
    "review": "output/stage3/question_candidates_reviewed.json",
    "answers": "output/stage4/answer_ir.json",
    "qa": "output/stage5/qa_report.json",
    "finalize": "output/stage6/reading_test1_demo.json",
}


def read_json(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def write_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_config(filename):
    path = Path(filename)

    if not path.is_absolute():
        path = ROOT / path

    config = read_json(path)
    dataset_id = config["id"]

    if not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]*",
        dataset_id,
    ):
        raise ValueError(
            "Invalid dataset ID"
        )

    pages = config["reading_pages"]

    if not (
        type(pages["start"]) is int
        and type(pages["end"]) is int
        and 1 <= pages["start"] <= pages["end"]
    ):
        raise ValueError(
            "Invalid Reading page range"
        )

    if type(config["answer_page"]) is not int:
        raise ValueError(
            "Invalid answer key page"
        )

    groups = config.get("groups") or []

    if not groups:
        raise ValueError(
            "At least one question group is required "
            "by the current structure detector"
        )

    numbers = [
        number
        for first, last, kind, page in groups
        for number in range(first, last + 1)
    ]

    if numbers != list(range(1, 41)):
        raise ValueError(
            "Question group ranges must cover Q1-Q40"
        )

    if len(config["answer_reference"]) != 40:
        raise ValueError(
            "Expected 40 reference answers"
        )

    if len(config["passage_headings"]) != 3:
        raise ValueError(
            "Expected three passage headings"
        )

    if len(config["passage_titles"]) != 3:
        raise ValueError(
            "Expected three passage titles"
        )

    return config


def source_path(filename):
    path = Path(filename)

    return (
        path if path.is_absolute()
        else ROOT / path
    )


def workspace(config):
    return (
        ROOT / "workspaces" / config["id"]
    )


def script_path(command):
    path = ROOT / SCRIPTS[command]

    if not path.is_file():
        raise FileNotFoundError(path)

    return path


def load_module(command):
    path = script_path(command)

    spec = importlib.util.spec_from_file_location(
        f"ielts_pipeline_{command}",
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Cannot load module: {path}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def compile_check():
    for command in SCRIPTS:
        path = script_path(command)

        source = path.read_text(
            encoding="utf-8"
        )

        compile(
            source,
            str(path),
            "exec",
        )

        print(f"OK: {command}")


def dataset_check(config):
    path = (
        workspace(config)
        / OUTPUTS["finalize"]
    )

    if not path.is_file():
        print(
            "Workspace dataset: not created yet"
        )
        return

    data = read_json(path)

    passages = data.get("passages") or []

    groups = [
        group
        for passage in passages
        for group in passage["question_groups"]
    ]

    questions = [
        question
        for group in groups
        for question in group["questions"]
    ]

    slots = []
    marks = 0

    for question in questions:
        numbers = question.get("numbers")

        if numbers is None:
            numbers = [question["number"]]

        if (
            not isinstance(numbers, list)
            or not numbers
            or any(
                type(number) is not int
                for number in numbers
            )
        ):
            raise ValueError(
                f"Invalid question slots: {numbers}"
            )

        score = question.get(
            "max_score",
            len(numbers),
        )

        if (
            type(score) is not int
            or score != len(numbers)
        ):
            raise ValueError(
                f"Invalid marks for slots {numbers}"
            )

        slots.extend(numbers)
        marks += score

    expected_groups = config.get("groups")

    if (
        len(passages) != 3
        or (
            expected_groups is not None
            and len(groups) != len(expected_groups)
        )
        or sorted(slots) != list(range(1, 41))
        or marks != 40
    ):
        raise ValueError(
            "Workspace dataset coverage invalid"
        )

    print(
        "Workspace dataset: "
        f"{len(passages)} passages / "
        f"{len(groups)} groups / "
        f"{len(slots)} slots / "
        f"{len(questions)} records / "
        f"{marks} marks"
    )


def migrate_legacy(config):
    target_root = workspace(config)
    copied = 0

    for number in range(1, 7):
        relative = (
            Path("output") / f"stage{number}"
        )

        old = ROOT / relative
        new = target_root / relative

        if not old.is_dir():
            print(
                f"SKIP (source missing): {relative}"
            )
            continue

        if new.exists():
            print(
                f"SKIP (already migrated): {relative}"
            )
            continue

        new.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copytree(old, new)
        copied += 1

        print(f"COPIED: {relative}")

    print(
        f"\nMigrated stage folders: {copied}"
    )
    print("Workspace:", target_root)


def configure_module(command, module, config):
    pages = list(
        range(
            config["reading_pages"]["start"],
            config["reading_pages"]["end"] + 1,
        )
    )

    if command == "extract":
        module.READING_PAGES = pages
        module.ANSWER_PAGE = config["answer_page"]

        module.EXPECTED_HEADINGS = {
            int(page): pattern
            for page, pattern in
            config["passage_headings"].items()
        }

    elif command == "structure":
        module.READING_PAGES = pages

        module.EXPECTED_GROUPS = [
            (first, last)
            for first, last, kind, page
            in config["groups"]
        ]

    elif command == "questions":
        module.CHOICE_KEYS = {
            kind: list(keys)
            for kind, keys in
            config["choice_keys"].items()
        }

        module.EXPECTED_GROUP_COUNT = len(
            config["groups"]
        )

    elif command == "review":
        module.EXPECTED_GROUP_COUNT = len(
            config["groups"]
        )

        review = source_path(
            config["review_file"]
        )

        if not review.is_file():
            raise FileNotFoundError(review)

        module.REVIEW = review

    elif command == "answers":
        page = config["answer_page"]

        module.ANSWER_PAGE = page

        module.EXPECTED_GROUP_COUNT = len(
            config["groups"]
        )

        module.PAGE = Path(
            f"output/stage1/pages/page_{page:03d}.json"
        )

        # Compatibility with the current mapper.
        module.REFERENCE = {
            number: module.normalize(answer)
            for number, answer in enumerate(
                config["answer_reference"], 1
            )
        }

    elif command == "qa":
        module.CHOICES = {
            kind: list(keys)
            for kind, keys in
            config["choice_keys"].items()
        }

        module.SPEC = [
            (first, last, kind, page)
            for first, last, kind, page
            in config["groups"]
        ]

    elif command == "finalize":
        module.EXPECTED_GROUP_COUNT = len(
            config["groups"]
        )

        module.TEST_TITLE = config["title"]
        module.ANSWER_PAGE = config["answer_page"]

        module.TITLES = {
            int(number): title
            for number, title in
            config["passage_titles"].items()
        }

        module.DISPLAY_TITLES = {
            int(number): title
            for number, title in
            config.get(
                "passage_display_titles", {}
            ).items()
        }

        module.PASSAGE_GLOSSARY = {
            int(number): entries
            for number, entries in
            config.get("glossary", {}).items()
        }

    elif command == "import":
        module.TITLE = config["title"]

        module.SOURCE = (
            workspace(config)
            / OUTPUTS["finalize"]
        )


def invoke(command, module, config, apply=False):
    working_dir = workspace(config)

    working_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    argv = [str(script_path(command))]

    if command == "extract":
        pdf = source_path(
            config["source_pdf"]
        )

        if not pdf.is_file():
            raise FileNotFoundError(pdf)

        argv.extend([
            "--pdf", str(pdf),
            "--out", "output/stage1",
        ])

    if command == "import" and apply:
        argv.extend([
            "--apply",
            "--confirm-create-draft",
        ])

    old_dir = Path.cwd()
    old_argv = sys.argv[:]

    try:
        os.chdir(working_dir)
        sys.argv = argv
        module.main()
    finally:
        sys.argv = old_argv
        os.chdir(old_dir)


def inspect_qa(config):
    path = (
        workspace(config)
        / OUTPUTS["qa"]
    )

    if not path.is_file():
        raise FileNotFoundError(
            f"Stage 5 report missing: {path}"
        )

    report = read_json(path)

    errors = report.get("errors") or []
    warnings = report.get("warnings") or []

    if errors or report.get("status") == "BLOCKED":
        raise RuntimeError(
            f"Content QA has {len(errors)} errors"
        )

    return warnings


def verify_final(config):
    path = (
        workspace(config)
        / OUTPUTS["finalize"]
    )

    data = read_json(path)

    if data.get("test") != config["title"]:
        raise ValueError(
            "Final dataset title mismatch"
        )

    if data.get("import_ready") is not False:
        raise ValueError(
            "Finalize must not authorize import"
        )

    if len(data.get("passages", [])) != 3:
        raise ValueError(
            "Invalid finalized passages"
        )

    for passage in data["passages"]:
        if re.search(
            r"\blmow\b",
            passage["content"],
        ):
            raise ValueError(
                "Uncorrected OCR artifact in "
                f"Passage {passage['number']}"
            )

    dataset_check(config)


def run_stage(
    command,
    config,
    force=False,
    apply=False,
):
    target = (
        workspace(config) / OUTPUTS[command]
        if command in OUTPUTS
        else None
    )

    if (
        target is not None
        and target.exists()
        and not force
    ):
        raise FileExistsError(
            f"{target} already exists. "
            "Use --force to rebuild intentionally."
        )

    module = load_module(command)
    configure_module(command, module, config)

    print(
        f"\n========== {command.upper()} =========="
    )

    invoke(
        command,
        module,
        config,
        apply=apply,
    )

    if (
        target is not None
        and not target.is_file()
    ):
        raise RuntimeError(
            f"Stage output missing: {target}"
        )

    if command == "qa":
        warnings = inspect_qa(config)
        print("QA warnings:", len(warnings))

    if command == "finalize":
        verify_final(config)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "check",
            "migrate",
            "all",
            *SCRIPTS.keys(),
        ],
    )

    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
    )

    parser.add_argument(
        "--force",
        action="store_true",
    )

    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate unapproved Stage 6 preview",
    )

    parser.add_argument(
        "--ack-review",
        action="store_true",
        help="Acknowledge QA review, not import approval",
    )

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    parser.add_argument(
        "--confirm-create-draft",
        action="store_true",
    )

    args = parser.parse_args()
    config = load_config(args.config)

    if args.preview and args.command != "finalize":
        parser.error(
            "--preview is only valid with finalize"
        )

    if args.preview and args.ack_review:
        parser.error(
            "Use --preview or --ack-review, not both"
        )

    if args.apply and args.command != "import":
        parser.error(
            "--apply is only valid with import"
        )

    if (
        args.confirm_create_draft
        and args.command != "import"
    ):
        parser.error(
            "--confirm-create-draft is import-only"
        )

    if args.command == "check":
        compile_check()
        dataset_check(config)
        print("\nCHECK PASS")
        return

    if args.command == "migrate":
        migrate_legacy(config)
        return

    if args.command == "import":
        ir_path = (
            workspace(config)
            / OUTPUTS["answers"]
        )

        if not ir_path.is_file():
            raise RuntimeError(
                "IMPORT BLOCKED: Answer IR missing"
            )

        if args.apply and not args.confirm_create_draft:
            parser.error(
                "--apply requires --confirm-create-draft"
            )

        run_stage(
            "import",
            config,
            apply=args.apply,
        )
        return

    if args.command == "all":
        for command in ORDER:
            if command == "finalize":
                warnings = inspect_qa(config)

                if warnings and not args.ack_review:
                    raise RuntimeError(
                        "QA warnings require manual review. "
                        "Use individual finalize --preview "
                        "to create an unapproved preview."
                    )

            run_stage(
                command,
                config,
                force=args.force,
            )

        print(
            "\nPIPELINE COMPLETE - "
            "CONTENT APPROVAL STILL REQUIRED"
        )
        return

    if args.command == "finalize":
        warnings = inspect_qa(config)

        if warnings and not (
            args.preview or args.ack_review
        ):
            raise RuntimeError(
                "QA warnings remain. "
                "Use --preview to generate a dataset "
                "without approving its content."
            )

        if args.preview:
            ir_path = (
                workspace(config)
                / OUTPUTS["answers"]
            )

            qa_path = (
                workspace(config)
                / OUTPUTS["qa"]
            )

            ir = read_json(ir_path)
            qa = read_json(qa_path)

            if (
                ir.get("status")
                != "ANSWER_IR_VALIDATED_IMPORT_BLOCKED"
                or qa.get("import_ready") is not False
            ):
                raise RuntimeError(
                    "Preview requires validated Answer IR "
                    "and explicitly blocked import"
                )

            print(
                f"PREVIEW MODE: {len(warnings)} QA warnings "
                "remain unapproved."
            )

    run_stage(
        args.command,
        config,
        force=args.force,
    )


if __name__ == "__main__":
    main()
