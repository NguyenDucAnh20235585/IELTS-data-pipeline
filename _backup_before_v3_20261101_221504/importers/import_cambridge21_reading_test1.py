
import argparse
import os
import re
import json
import hashlib
from pathlib import Path

SOURCE = Path("output/stage6/reading_test1_demo.json")
TITLE = "Cambridge IELTS 21 - Reading Test 1"

TYPE_MAP = {
    "NOTE_COMPLETION": "note_completion",
    "TRUE_FALSE_NOT_GIVEN": "true_false_not_given",
    "MATCHING_INFORMATION": "matching_information",
    "SUMMARY_COMPLETION": "summary_completion",
    "MATCHING_FEATURES": "matching_features",
    "MULTIPLE_CHOICE": "mcq_single",
    "SUMMARY_WORD_BANK": "summary_completion",
    "YES_NO_NOT_GIVEN": "yes_no_not_given",
}

INLINE_TYPES = {
    "NOTE_COMPLETION",
    "SUMMARY_COMPLETION",
    "SUMMARY_WORD_BANK",
}


def compact(value):
    return re.sub(r"\s+", " ", value).strip()


def clean_passage(passage):
    content = passage["content"].strip()
    title = passage["title"]

    if content.startswith(title):
        content = content[len(title):].strip()

    if passage["number"] == 3:
        content = re.sub(
            r"^Book review:\s*The World of Sugar by"
            r"\s+Ulbe Bosma\s*",
            "",
            content,
            count=1,
        )

    # Remaining confirmed PDF extraction error.
    content = content.replace(
        "we lmow also", "we know also"
    )

    if "lmow" in content:
        raise ValueError(
            f"Unresolved OCR artifact in Passage "
            f"{passage['number']}"
        )

    glossary = passage.get("glossary") or []

    if glossary:
        content += "\n\nGlossary:\n"
        content += "\n".join(
            f"{item['term']}: {item['definition']}"
            for item in glossary
        )

    if len(content) < 1500:
        raise ValueError("Suspiciously short passage")

    return content


def prepare_group(group):
    kind = group["question_type_hint"]

    if kind not in TYPE_MAP:
        raise ValueError(f"Unsupported type: {kind}")

    first = group["number_from"]
    last = group["number_to"]

    shared = group.get("shared_options") or []
    template = group.get("render_template") or ""

    instructions = group.get("instructions")
    content = None

    if kind in INLINE_TYPES:
        if not template:
            raise ValueError(
                f"Q{first}-{last}: missing template"
            )

        lines = template.splitlines()
        count = 2 if kind == "SUMMARY_WORD_BANK" else 3

        instructions = compact("\n".join(lines[:count]))
        content = "\n".join(lines[count:]).strip()

        # Demo presentation: numbered blanks appear in
        # the group content; answers use question cards.
        for n in range(first, last + 1):
            token = "{{gap:" + str(n) + "}}"

            if content.count(token) != 1:
                raise ValueError(
                    f"Q{n}: missing or duplicate gap"
                )

            content = content.replace(
                token, f"[{n}: ______]"
            )

    prepared_questions = []

    for position, q in enumerate(group["questions"], 1):
        n = q["number"]

        if n != first + position - 1:
            raise ValueError(f"Question order error: Q{n}")

        answer_format = q["answer_format"].lower()

        if answer_format not in ("text", "choice"):
            raise ValueError(
                f"Q{n}: invalid answer format"
            )

        if q.get("render_mode") == "INLINE_GAP":
            unit = (
                "notes" if kind == "NOTE_COMPLETION"
                else "summary"
            )

            if answer_format == "choice":
                prompt = (
                    f"Choose the correct letter for "
                    f"blank {n} in the {unit} above."
                )
            else:
                prompt = (
                    f"Enter ONE WORD ONLY for blank "
                    f"{n} in the {unit} above."
                )
        else:
            prompt = compact(q.get("prompt") or "")

        if not prompt:
            raise ValueError(f"Q{n}: empty prompt")

        options = q.get("options") or shared or None
        answer = q["correct_answer"]

        if answer_format == "choice":
            if not isinstance(options, list) or not options:
                raise ValueError(
                    f"Q{n}: missing choice options"
                )

            keys = {o["key"] for o in options}

            if answer.get("choice") not in keys:
                raise ValueError(
                    f"Q{n}: answer not in options"
                )
        else:
            accepted = answer.get("accepted")

            if not isinstance(accepted, list) or not accepted:
                raise ValueError(
                    f"Q{n}: missing text answers"
                )

        prepared_questions.append({
            "number": n,
            "position": position,
            "question_type": TYPE_MAP[kind],
            "answer_format": answer_format,
            "prompt": prompt,
            "options": options,
            "config": q.get("config") or {},
            "correct_answer": answer,
        })

    return {
        "title": f"Questions {first}-{last}",
        "instructions": instructions,
        "content": content,
        "options": shared or None,
        "questions": prepared_questions,
    }


def prepare_data():
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)

    raw = SOURCE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    data = json.loads(raw.decode("utf-8"))
    passages = data["passages"]

    if len(passages) != 3:
        raise ValueError("Expected three passages")

    result = []
    numbers = []

    for expected_position, passage in enumerate(passages, 1):
        if passage["number"] != expected_position:
            raise ValueError("Passage order mismatch")

        groups = [
            prepare_group(g)
            for g in passage["question_groups"]
        ]

        for group in groups:
            numbers.extend(
                q["number"] for q in group["questions"]
            )

        result.append({
            "title": passage["title"],
            "content": clean_passage(passage),
            "groups": groups,
        })

    if numbers != list(range(1, 41)):
        raise ValueError(
            f"Invalid question numbering: {numbers}"
        )

    if sum(len(p["groups"]) for p in result) != 8:
        raise ValueError("Expected eight question groups")

    return result, digest


def import_draft(passages):
    import psycopg
    from psycopg.types.json import Jsonb

    url = os.getenv("DATABASE_URL")

    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is missing"
        )

    # Transaction starts automatically. The connection
    # commits only if the entire import succeeds.
    with psycopg.connect(
        url,
        sslmode="require",
        connect_timeout=20,
        prepare_threshold=None,
    ) as conn:

        with conn.cursor() as cur:

            # Prevent concurrent runs of this importer.
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
                    "Multiple tests have this title. "
                    "Manual review required."
                )

            if existing:
                test_id = existing[0][0]

                cur.execute(
                    """
                    SELECT id FROM public.test_versions
                    WHERE test_id = %s AND status = 'draft'
                    """,
                    (test_id,),
                )

                if cur.fetchone():
                    raise RuntimeError(
                        "An existing Draft was found. "
                        "Import stopped to prevent duplication."
                    )

                cur.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1
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
                    VALUES (%s, %s, 'reading',
                            'hidden', 'public')
                    RETURNING id
                    """,
                    (
                        TITLE,
                        "Cambridge 21 Reading Test 1 "
                        "- imported for demo review",
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

            # Practice mode only for initial demo.
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

            for section_pos, passage in enumerate(passages, 1):
                cur.execute(
                    """
                    INSERT INTO public.sections
                        (test_version_id, section_type,
                         title, content, position)
                    VALUES (%s, 'reading', %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        version_id,
                        passage["title"],
                        passage["content"],
                        section_pos,
                    ),
                )
                section_id = cur.fetchone()[0]

                for group_pos, group in enumerate(
                    passage["groups"], 1
                ):
                    cur.execute(
                        """
                        INSERT INTO public.question_groups
                            (section_id, title,
                             instructions, content,
                             options, rules, position)
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
                            Jsonb({}),
                            group_pos,
                        ),
                    )
                    group_id = cur.fetchone()[0]

                    for q in group["questions"]:
                        cur.execute(
                            """
                            INSERT INTO public.questions
                                (question_group_id,
                                 question_type, prompt,
                                 config, position,
                                 max_score, answer_format,
                                 options, display_number)
                            VALUES
                                (%s, %s, %s, %s, %s,
                                 1, %s, %s, %s)
                            RETURNING id
                            """,
                            (
                                group_id,
                                q["question_type"],
                                q["prompt"],
                                Jsonb(q["config"]),
                                q["position"],
                                q["answer_format"],
                                (
                                    Jsonb(q["options"])
                                    if q["options"] is not None
                                    else None
                                ),
                                q["number"],
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
                                Jsonb(q["correct_answer"]),
                                Jsonb({}),
                            ),
                        )

            # Verify actual database rows before commit.
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

            question_count, marks, keys, numbers = cur.fetchone()

            if (
                question_count != 40
                or marks != 40
                or keys != 40
                or numbers != list(range(1, 41))
            ):
                raise RuntimeError(
                    "Post-import verification failed. "
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

            if section_count != 3 or group_count != 8:
                raise RuntimeError(
                    "Section/group count mismatch. "
                    "Transaction will roll back."
                )

            print("\n--- DATABASE VERIFIED ---")
            print("Test ID:", test_id)
            print("Version ID:", version_id)
            print("Version number:", version_number)
            print("Status: draft")
            print("Sections:", section_count)
            print("Groups:", group_count)
            print("Questions:", question_count)
            print("Answer keys:", keys)
            print("Marks:", marks)

    print("\nIMPORT COMMITTED SUCCESSFULLY")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to Supabase",
    )
    args = parser.parse_args()

    passages, digest = prepare_data()

    groups = sum(len(p["groups"]) for p in passages)
    questions = sum(
        len(g["questions"])
        for p in passages
        for g in p["groups"]
    )

    print("\n--- SOURCE VALIDATION ---")
    print("Source:", SOURCE)
    print("SHA256:", digest)
    print("Passages:", len(passages))
    print("Groups:", groups)
    print("Questions:", questions)
    print("Expected marks:", questions)

    if not args.apply:
        print("\nDRY RUN PASS - NO DATABASE CHANGES")
        return

    import_draft(passages)


if __name__ == "__main__":
    main()
