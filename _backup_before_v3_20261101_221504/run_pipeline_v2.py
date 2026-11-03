
import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEFAULT_CONFIG = (
    "configs/cambridge21_reading_test1.json"
)

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
    "answers": "output/stage4/reading_test1_mapped.json",
    "qa": "output/stage5/qa_report.json",
    "finalize": "output/stage6/reading_test1_demo.json",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_config(filename):
    path = Path(filename)

    if not path.is_absolute():
        path = ROOT / path

    data = read_json(path)

    dataset_id = data["id"]

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", dataset_id):
        raise ValueError("Invalid dataset ID")

    pages = data["reading_pages"]

    if not (
        isinstance(pages["start"], int)
        and isinstance(pages["end"], int)
        and 1 <= pages["start"] <= pages["end"]
    ):
        raise ValueError("Invalid reading page range")

    if not isinstance(data["answer_page"], int):
        raise ValueError("Invalid answer page")

    groups = data["groups"]

    if len(groups) != 8:
        raise ValueError("Pilot engine expects 8 groups")

    numbers = [
        n
        for first, last, kind, page in groups
        for n in range(first, last + 1)
    ]

    if numbers != list(range(1, 41)):
        raise ValueError("Pilot engine expects Q1-Q40")

    if len(data["answer_reference"]) != 40:
        raise ValueError("Expected 40 reference answers")

    if len(data["passage_headings"]) != 3:
        raise ValueError("Expected three passage headings")

    if len(data["passage_titles"]) != 3:
        raise ValueError("Expected three passage titles")

    return data


def source_path(filename):
    path = Path(filename)
    return path if path.is_absolute() else ROOT / path


def workspace(config):
    return ROOT / "workspaces" / config["id"]


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
        raise RuntimeError(f"Cannot load {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def compile_check():
    for command in SCRIPTS:
        path = script_path(command)
        source = path.read_text(encoding="utf-8")

        compile(source, str(path), "exec")
        print(f"OK: {command}")


def dataset_check(config):
    path = workspace(config) / OUTPUTS["finalize"]

    if not path.is_file():
        print("Workspace dataset: not created yet")
        return

    data = read_json(path)
    passages = data["passages"]

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

    numbers = sorted(q["number"] for q in questions)

    if (
        len(passages) != 3
        or len(groups) != 8
        or numbers != list(range(1, 41))
    ):
        raise ValueError("Workspace dataset coverage invalid")

    print(
        "Workspace dataset: "
        "3 passages / 8 groups / 40 questions"
    )


def migrate_legacy(config):
    target_root = workspace(config)
    copied = 0

    for number in range(1, 7):
        relative = Path("output") / f"stage{number}"

        old = ROOT / relative
        new = target_root / relative

        if not old.is_dir():
            print(f"SKIP (source missing): {relative}")
            continue

        if new.exists():
            print(f"SKIP (already migrated): {relative}")
            continue

        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(old, new)

        copied += 1
        print(f"COPIED: {relative}")

    print(f"\nMigrated stage folders: {copied}")
    print(f"Workspace: {target_root}")
    print("Legacy output unchanged")


def configure_module(command, module, config):
    pages = list(range(
        config["reading_pages"]["start"],
        config["reading_pages"]["end"] + 1,
    ))

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

    elif command == "review":
        path = source_path(config["review_file"])

        if not path.is_file():
            raise FileNotFoundError(path)

        module.REVIEW = path

    elif command == "answers":
        page = config["answer_page"]

        module.PAGE = Path(
            f"output/stage1/pages/page_{page:03d}.json"
        )

        module.REFERENCE = {
            n: module.normalize(answer)
            for n, answer in enumerate(
                config["answer_reference"], 1
            )
        }

    elif command == "qa":
        module.SPEC = [
            (first, last, kind, page)
            for first, last, kind, page
            in config["groups"]
        ]

    elif command == "finalize":
        module.TITLES = {
            int(n): title
            for n, title in
            config["passage_titles"].items()
        }

        module.PASSAGE_FIXES = {
            int(n): fixes
            for n, fixes in
            config["passage_fixes"].items()
        }

    elif command == "import":
        module.TITLE = config["title"]
        module.SOURCE = (
            workspace(config) / OUTPUTS["finalize"]
        )


def invoke(command, module, config, apply=False):
    work = workspace(config)
    work.mkdir(parents=True, exist_ok=True)

    path = script_path(command)

    argv = [str(path)]

    if command == "extract":
        pdf = source_path(config["source_pdf"])

        if not pdf.is_file():
            raise FileNotFoundError(pdf)

        argv.extend([
            "--pdf", str(pdf),
            "--out", "output/stage1",
        ])

    if command == "import" and apply:
        argv.append("--apply")

    previous_dir = Path.cwd()
    previous_argv = sys.argv[:]

    try:
        os.chdir(work)
        sys.argv = argv
        module.main()
    finally:
        sys.argv = previous_argv
        os.chdir(previous_dir)


def inspect_qa(config):
    path = workspace(config) / OUTPUTS["qa"]
    data = read_json(path)

    errors = data.get("errors", [])
    warnings = data.get("warnings", [])

    if errors:
        raise RuntimeError(
            f"Content QA has {len(errors)} errors"
        )

    return warnings


def verify_final(config):
    path = workspace(config) / OUTPUTS["finalize"]
    data = read_json(path)

    data["test"] = config["title"]

    if len(data.get("passages", [])) != 3:
        raise ValueError("Invalid finalized passages")

    for passage in data["passages"]:
        if re.search(r"\blmow\b", passage["content"]):
            raise ValueError(
                "Uncorrected OCR artifact in "
                f"Passage {passage['number']}"
            )

    write_json(path, data)
    dataset_check(config)


def run_stage(command, config, force=False, apply=False):
    target = (
        workspace(config) / OUTPUTS[command]
        if command in OUTPUTS
        else None
    )

    if target is not None and target.exists() and not force:
        raise FileExistsError(
            f"{target} already exists. "
            "Use --force only when intentionally rebuilding."
        )

    module = load_module(command)
    configure_module(command, module, config)

    print(f"\n========== {command.upper()} ==========")
    invoke(command, module, config, apply=apply)

    if target is not None and not target.is_file():
        raise RuntimeError(
            f"Stage did not produce expected output: {target}"
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
        help="Allow overwriting workspace stage outputs",
    )

    parser.add_argument(
        "--ack-review",
        action="store_true",
        help="Acknowledge already reviewed QA warnings",
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to database (import only)",
    )

    parser.add_argument(
        "--confirm-create-draft",
        action="store_true",
        help="Explicitly confirm creating a new Draft",
    )

    args = parser.parse_args()
    config = load_config(args.config)

    if args.apply and args.command != "import":
        parser.error("--apply is only valid with import")

    if args.command == "check":
        compile_check()
        dataset_check(config)
        print("\nCHECK PASS")
        return

    if args.command == "migrate":
        migrate_legacy(config)
        return

    if args.command == "import":
        if args.apply and not args.confirm_create_draft:
            parser.error(
                "--apply requires --confirm-create-draft"
            )

        # Existing Drafts are additionally protected
        # by the importer itself.
        run_stage("import", config, apply=args.apply)
        return

    if args.command == "all":
        for command in ORDER:
            if command == "finalize":
                warnings = inspect_qa(config)

                if warnings and not args.ack_review:
                    raise RuntimeError(
                        "QA warnings require manual PDF review. "
                        "Use --ack-review only after checking them."
                    )

            run_stage(
                command,
                config,
                force=args.force,
            )

        print("\nPIPELINE COMPLETE - PREVIEW STILL REQUIRED")
        return

    if args.command == "finalize":
        warnings = inspect_qa(config)

        if warnings and not args.ack_review:
            raise RuntimeError(
                "QA warnings require --ack-review "
                "after manual verification"
            )

    run_stage(
        args.command,
        config,
        force=args.force,
    )


if __name__ == "__main__":
    main()
