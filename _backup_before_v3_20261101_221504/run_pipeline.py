
import argparse
import json
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

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


def check():
    for name, relative in SCRIPTS.items():
        path = ROOT / relative

        if not path.is_file():
            raise FileNotFoundError(path)

        py_compile.compile(str(path), doraise=True)
        print(f"OK: {name}")

    dataset = ROOT / "output/stage6/reading_test1_demo.json"

    if dataset.exists():
        data = json.loads(dataset.read_text(encoding="utf-8"))
        passages = data["passages"]
        groups = [
            g for p in passages
            for g in p["question_groups"]
        ]
        questions = [
            q for g in groups
            for q in g["questions"]
        ]

        numbers = sorted(q["number"] for q in questions)

        if (
            len(passages) != 3
            or len(groups) != 8
            or numbers != list(range(1, 41))
        ):
            raise ValueError("Dataset coverage check failed")

        print("\nReading Test 1: 3 passages / 8 groups / 40 questions")

    print("\nSTRUCTURE CHECK PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=["check", *SCRIPTS.keys()],
    )
    args, extra = parser.parse_known_args()

    if args.command == "check":
        if extra:
            parser.error("check does not accept extra arguments")
        check()
        return

    if args.command == "import" and "--apply" in extra:
        parser.error(
            "Draft already imported. "
            "Database writes are disabled in this runner."
        )

    script = ROOT / SCRIPTS[args.command]

    subprocess.run(
        [sys.executable, str(script), *extra],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
