
"""Generic IELTS Reading Draft importer.

Dry-run by default.
Database writes require explicit content approval.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path


SOURCE = Path("output/stage6/reading_test1_demo.json")
TITLE = "IELTS Reading Test"


def load(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def sha256(path):
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def prepare_data():
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)

    data = load(SOURCE)

    if data.get("contract_version") != 1:
        raise ValueError(
            "Unsupported dataset contract version"
        )

    if data.get("test") != TITLE:
        raise ValueError(
            "Dataset title differs from import target"
        )

    raw_passages = data.get("passages")

    if (
        not isinstance(raw_passages, list)
        or len(raw_passages) != 3
    ):
        raise ValueError(
            "Expected exactly three Reading passages"
        )

    passages = []
    slots = []
    display_numbers = []
    marks = 0

    for section_pos, passage in enumerate(
        raw_passages, 1
    ):
        if passage.get("number") != section_pos:
            raise ValueError(
                "Passage order mismatch"
            )

        title = passage.get("title")
        content = (
            passage.get("content") or ""
        ).strip()

        if not title or len(content) < 1500:
            raise ValueError(
                f"Passage {section_pos}: invalid content"
            )

        if content.startswith(title):
            content = content[len(title):].strip()

        glossary = passage.get("glossary") or []

        if glossary:
            content += "\n\nGlossary:\n"
            content += "\n".join(
                f"{entry['term']}: {entry['definition']}"
                for entry in glossary
            )

        groups = []

        for group_pos, group in enumerate(
            passage.get("question_groups", []), 1
        ):
            first = group["number_from"]
            last = group["number_to"]
            expected = list(range(first, last + 1))

            group_slots = []
            prepared_questions = []

            shared_options = (
                group.get("options") or None
            )

            for question_pos, question in enumerate(
                group.get("questions", []), 1
            ):
                numbers = question.get("numbers")
                max_score = question.get("max_score")
                answer_format = question.get("answer_format")
                correct = question.get(
                    "correct_answer"
                ) or {}

                options = (
                    question.get("options")
                    or shared_options
                )

                if (
                    not isinstance(numbers, list)
                    or not numbers
                    or any(
                        type(n) is not int
                        for n in numbers
                    )
                    or numbers != list(
                        range(
                            numbers[0],
                            numbers[0] + len(numbers),
                        )
                    )
                ):
                    raise ValueError(
                        f"Invalid question numbers: {numbers}"
                    )

                if (
                    type(max_score) is not int
                    or max_score != len(numbers)
                    or max_score < 1
                ):
                    raise ValueError(
                        f"Q{numbers}: invalid max_score"
                    )

                if (
                    question.get("position")
                    != question_pos
                    or question.get("display_number")
                    != numbers[0]
                ):
                    raise ValueError(
                        f"Q{numbers}: position mismatch"
                    )

                question_type = question.get(
                    "question_type"
                )
                prompt = question.get("prompt")

                if (
                    not isinstance(question_type, str)
                    or not question_type
                    or not isinstance(prompt, str)
                    or not prompt.strip()
                ):
                    raise ValueError(
                        f"Q{numbers}: incomplete question"
                    )

                if answer_format == "text":
                    accepted = correct.get("accepted")

                    if (
                        max_score != 1
                        or not isinstance(accepted, list)
                        or not accepted
                        or any(
                            not isinstance(value, str)
                            or not value.strip()
                            for value in accepted
                        )
                    ):
                        raise ValueError(
                            f"Q{numbers}: invalid text answer"
                        )

                    word_limit = (
                        question.get("config") or {}
                    ).get("max_words")

                    if word_limit is not None:
                        if (
                            type(word_limit) is not int
                            or word_limit < 1
                        ):
                            raise ValueError(
                                f"Q{numbers}: invalid word limit"
                            )

                        allow_number = (
                            question.get("config") or {}
                        ).get("allow_number", False)

                        for value in accepted:
                            tokens = value.split()

                            numeric = sum(
                                token.replace(".", "", 1).isdigit()
                                for token in tokens
                            )

                            if allow_number:
                                valid = (
                                    len(tokens) - numeric
                                    <= word_limit
                                    and numeric <= 1
                                )
                            else:
                                valid = (
                                    len(tokens) <= word_limit
                                )

                            if not valid:
                                raise ValueError(
                                    f"Q{numbers}: answer exceeds "
                                    "word limit"
                                )

                elif answer_format in (
                    "choice", "multi_choice"
                ):
                    if not isinstance(options, list) or not options:
                        raise ValueError(
                            f"Q{numbers}: missing options"
                        )

                    keys = [
                        option["key"]
                        for option in options
                    ]

                    if len(keys) != len(set(keys)):
                        raise ValueError(
                            f"Q{numbers}: duplicate options"
                        )

                    if answer_format == "choice":
                        if (
                            max_score != 1
                            or correct.get("choice") not in keys
                        ):
                            raise ValueError(
                                f"Q{numbers}: invalid choice"
                            )

                    else:
                        choices = correct.get("choices")

                        if (
                            question_type != "mcq_multi"
                            or not isinstance(choices, list)
                            or len(choices) != max_score
                            or len(set(choices)) != max_score
                            or not set(choices).issubset(set(keys))
                            or (
                                question.get("config") or {}
                            ).get("select_count") != max_score
                        ):
                            raise ValueError(
                                f"Q{numbers}: invalid multi-choice"
                            )

                else:
                    raise ValueError(
                        f"Q{numbers}: unknown answer format"
                    )

                group_slots.extend(numbers)
                display_numbers.append(numbers[0])
                marks += max_score

                prepared_questions.append({
                    "position": question_pos,
                    "display_number": numbers[0],
                    "question_type": question_type,
                    "prompt": prompt,
                    "answer_format": answer_format,
                    "max_score": max_score,
                    "config": question.get("config") or {},
                    "options": options,
                    "correct_answer": correct,
                    "grading_config": (
                        question.get("grading_config") or {}
                    ),
                })

            if group_slots != expected:
                raise ValueError(
                    f"Q{first}-{last}: group coverage mismatch"
                )

            slots.extend(group_slots)

            groups.append({
                "title": (
                    group.get("title")
                    or f"Questions {first}-{last}"
                ),
                "instructions": group.get("instructions"),
                "content": group.get("content"),
                "options": shared_options,
                "rules": {
                    "layout": group.get("render_mode"),
                },
                "position": group_pos,
                "questions": prepared_questions,
            })

        passages.append({
            "title": title,
            "content": content,
            "position": section_pos,
            "groups": groups,
        })

    if (
        slots != list(range(1, 41))
        or marks != 40
        or len(set(display_numbers)) != len(display_numbers)
    ):
        raise ValueError(
            "Dataset does not cover 40 question slots/marks"
        )

    summary = {
        "passages": len(passages),
        "groups": sum(
            len(p["groups"]) for p in passages
        ),
        "records": len(display_numbers),
        "slots": len(slots),
        "marks": marks,
        "display_numbers": display_numbers,
    }

    return passages, summary


def content_approved():
    """Approval is bound to exact dataset and QA report bytes."""
    report_path = (
        SOURCE.parent / "finalize_report.json"
    )

    approval_path = (
        SOURCE.parent / "content_approval.json"
    )

    if (
        not report_path.is_file()
        or not approval_path.is_file()
    ):
        return False

    report = load(report_path)
    approval = load(approval_path)

    if report.get("requires_visual_approval") is not True:
        return False

    if report.get("import_ready") is not False:
        return False

    if report.get("status") not in (
        "CONTENT_REVIEW_REQUIRED",
        "PREVIEW_READY_PENDING_APPROVAL",
    ):
        return False

    review_items = sorted(
        report.get("review_items") or []
    )

    reviewed_items = sorted(
        approval.get("reviewed_items") or []
    )

    if review_items != reviewed_items:
        return False

    return (
        approval.get("status")
        == "APPROVED_FOR_DRAFT_IMPORT"
        and bool(approval.get("reviewed_by"))
        and bool(approval.get("approved_at"))
        and approval.get("dataset_sha256")
        == sha256(SOURCE)
        and approval.get("report_sha256")
        == sha256(report_path)
    )


def import_draft(passages, summary):
    import psycopg
    from psycopg.types.json import Jsonb

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable missing"
        )

    with psycopg.connect(
        database_url,
        sslmode="require",
        connect_timeout=20,
        prepare_threshold=None,
    ) as connection:

        with connection.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (TITLE,),
            )

            cur.execute(
                "SELECT id FROM public.tests WHERE title = %s",
                (TITLE,),
            )

            existing = cur.fetchall()

            if len(existing) > 1:
                raise RuntimeError(
                    "Multiple tests have the same title"
                )

            if existing:
                test_id = existing[0][0]

                cur.execute(
                    """
                    SELECT id
                    FROM public.test_versions
                    WHERE test_id = %s
                      AND status = 'draft'
                    """,
                    (test_id,),
                )

                if cur.fetchone():
                    raise RuntimeError(
                        "Existing Draft found. "
                        "Import stopped to avoid duplication."
                    )

                cur.execute(
                    """
                    SELECT COALESCE(
                        MAX(version_number), 0
                    ) + 1
                    FROM public.test_versions
                    WHERE test_id = %s
                    """,
                    (test_id,),
                )

                version_number = cur.fetchone()[0]

            else:
                cur.execute(
                    """
                    INSERT INTO public.tests
                        (title, description, type,
                         visibility, access_type)
                    VALUES
                        (%s, %s, 'reading', 'hidden', 'public')
                    RETURNING id
                    """,
                    (
                        TITLE,
                        "Imported IELTS Reading dataset",
                    ),
                )

                test_id = cur.fetchone()[0]
                version_number = 1

            cur.execute(
                """
                INSERT INTO public.test_versions
                    (test_id, version_number, status)
                VALUES (%s, %s, 'draft')
                RETURNING id
                """,
                (test_id, version_number),
            )

            version_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO public.test_version_modes
                    (test_version_id, mode, enabled,
                     answer_visibility, allow_pause,
                     allow_replay, allow_seek)
                VALUES
                    (%s, 'practice', true,
                     'after_submit', true, false, false)
                """,
                (version_id,),
            )

            for passage in passages:
                cur.execute(
                    """
                    INSERT INTO public.sections
                        (test_version_id, section_type,
                         title, content, position)
                    VALUES
                        (%s, 'reading', %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        version_id,
                        passage["title"],
                        passage["content"],
                        passage["position"],
                    ),
                )

                section_id = cur.fetchone()[0]

                for group in passage["groups"]:
                    cur.execute(
                        """
                        INSERT INTO public.question_groups
                            (section_id, title, instructions,
                             content, options, rules, position)
                        VALUES
                            (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            section_id,
                            group["title"],
                            group["instructions"],
                            group["content"],
                            (
                                Jsonb(group["options"])
                                if group["options"] is not None
                                else None
                            ),
                            Jsonb(group["rules"]),
                            group["position"],
                        ),
                    )

                    group_id = cur.fetchone()[0]

                    for question in group["questions"]:
                        cur.execute(
                            """
                            INSERT INTO public.questions
                                (question_group_id,
                                 question_type, prompt,
                                 config, position, max_score,
                                 answer_format, options,
                                 display_number)
                            VALUES
                                (%s, %s, %s, %s, %s,
                                 %s, %s, %s, %s)
                            RETURNING id
                            """,
                            (
                                group_id,
                                question["question_type"],
                                question["prompt"],
                                Jsonb(question["config"]),
                                question["position"],
                                question["max_score"],
                                question["answer_format"],
                                (
                                    Jsonb(question["options"])
                                    if question["options"] is not None
                                    else None
                                ),
                                question["display_number"],
                            ),
                        )

                        question_id = cur.fetchone()[0]

                        cur.execute(
                            """
                            INSERT INTO public.answer_keys
                                (question_id, correct_answer,
                                 grading_config)
                            VALUES (%s, %s, %s)
                            """,
                            (
                                question_id,
                                Jsonb(question["correct_answer"]),
                                Jsonb(question["grading_config"]),
                            ),
                        )

            # Verify the exact inserted dataset before commit.
            cur.execute(
                """
                SELECT
                    COUNT(*),
                    COALESCE(SUM(q.max_score), 0),
                    COUNT(ak.question_id),
                    ARRAY_AGG(
                        q.display_number
                        ORDER BY q.display_number
                    )
                FROM public.sections s
                JOIN public.question_groups g
                  ON g.section_id = s.id
                JOIN public.questions q
                  ON q.question_group_id = g.id
                LEFT JOIN public.answer_keys ak
                  ON ak.question_id = q.id
                WHERE s.test_version_id = %s
                """,
                (version_id,),
            )

            records, marks, keys, numbers = cur.fetchone()

            if (
                records != summary["records"]
                or marks != summary["marks"]
                or keys != summary["records"]
                or numbers != sorted(
                    summary["display_numbers"]
                )
            ):
                raise RuntimeError(
                    "Database question verification failed. "
                    "Transaction will roll back."
                )

            cur.execute(
                """
                SELECT
                    COUNT(DISTINCT s.id),
                    COUNT(DISTINCT g.id)
                FROM public.sections s
                LEFT JOIN public.question_groups g
                  ON g.section_id = s.id
                WHERE s.test_version_id = %s
                """,
                (version_id,),
            )

            section_count, group_count = cur.fetchone()

            if (
                section_count != summary["passages"]
                or group_count != summary["groups"]
            ):
                raise RuntimeError(
                    "Database section/group verification failed"
                )

            print("\n--- DATABASE VERIFIED ---")
            print("Sections:", section_count)
            print("Groups:", group_count)
            print("Question records:", records)
            print("Total marks:", marks)
            print("Answer keys:", keys)
            print("Draft version:", version_number)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
    )

    parser.add_argument(
        "--confirm-create-draft",
        action="store_true",
    )

    args = parser.parse_args()

    passages, summary = prepare_data()
    approved = content_approved()

    print("\n--- GENERIC READING IMPORTER ---")
    print("Test:", TITLE)
    print("Passages:", summary["passages"])
    print("Groups:", summary["groups"])
    print("Question slots:", summary["slots"])
    print("Gradable records:", summary["records"])
    print("Total marks:", summary["marks"])
    print("Content approved:", approved)

    if not args.apply:
        print(
            "DRY RUN COMPLETE - no database writes"
        )
        return

    if not args.confirm_create_draft:
        raise RuntimeError(
            "Explicit Draft confirmation required"
        )

    if not approved:
        raise RuntimeError(
            "IMPORT BLOCKED: missing or invalid "
            "content approval"
        )

    import_draft(passages, summary)


if __name__ == "__main__":
    main()
