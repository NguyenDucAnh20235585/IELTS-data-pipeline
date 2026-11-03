
"""
Cambridge 21 Reading Test 1 — Full 3 passages.

Copies Passage 1 from the existing published database version.
Adds Passages 2 and 3 from the reviewed JSON dataset.

Creates a separate hidden QA test and Draft version.

Default: dry-run only.
No existing test, version, attempt or answer is modified.
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path


TYPES = {
    "MATCHING_INFORMATION": "matching_information",
    "SUMMARY_COMPLETION": "summary_completion",
    "MATCHING_FEATURES": "matching_features",
    "MULTIPLE_CHOICE": "mcq_single",
    "SUMMARY_WORD_BANK": "summary_completion",
    "YES_NO_NOT_GIVEN": "yes_no_not_given",
}

GAP = re.compile(r"\{\{gap:(\d+)\}\}")


def compact(value):
    return re.sub(r"\s+", " ", value or "").strip()


def passage_body(passage):
    raw = passage["content"].strip()
    number = passage["number"]

    if number == 2:
        raw = re.sub(
            r"^Why we need silence\s*",
            "",
            raw,
            count=1,
        )

    elif number == 3:
        start = raw.find(
            "Ulbe Bosma's The World of Sugar"
        )

        if start < 0:
            raise ValueError(
                "Passage 3: cannot locate real opening"
            )

        raw = raw[start:]

    else:
        raise ValueError(
            "Only passages 2 and 3 can be imported"
        )

    parts = []
    segment = []

    for line in raw.splitlines():
        line = line.strip()

        if (
            number == 2
            and re.fullmatch(r"[A-G]", line)
        ):
            if segment:
                parts.append(
                    compact(" ".join(segment))
                )

            segment = [f"**{line}**"]

        elif not line:
            if segment:
                parts.append(
                    compact(" ".join(segment))
                )
                segment = []

        else:
            segment.append(line)

    if segment:
        parts.append(compact(" ".join(segment)))

    result = "\n\n".join(parts)

    if number == 2:
        for letter in "ABCDEFG":
            if result.count(
                f"**{letter}** "
            ) != 1:
                raise ValueError(
                    f"Passage 2: section {letter} "
                    "missing or duplicated"
                )

    if len(result) < 4000:
        raise ValueError(
            f"Passage {number}: body too short"
        )

    if (
        "ofthe" in result
        or "UlbeBosma" in result
    ):
        raise ValueError(
            "Unresolved OCR artifact in passage"
        )

    return result


def normalize_key(question_type, answer):
    result = dict(answer)

    if question_type in {
        "yes_no_not_given",
        "true_false_not_given",
    }:
        if "choice" in result:
            result["choice"] = (
                result["choice"].replace(" ", "_")
            )

    return result


def group_content(group):
    kind = group["question_type_hint"]

    if kind not in {
        "SUMMARY_COMPLETION",
        "SUMMARY_WORD_BANK",
    }:
        return compact(
            group.get("instructions")
        ), None

    template = group.get("render_template") or ""
    lines = template.splitlines()

    header_count = (
        3
        if kind == "SUMMARY_COMPLETION"
        else 2
    )

    if len(lines) <= header_count:
        raise ValueError(
            f"Q{group['number_from']}: "
            "missing inline template"
        )

    instructions = compact(
        " ".join(lines[:header_count])
    )

    body = compact(
        " ".join(lines[header_count:])
    )

    if kind == "SUMMARY_COMPLETION":
        heading = "Flotation Tanks"
    else:
        heading = "Sugar cultivation and production"

    if not body.startswith(heading + " "):
        raise ValueError(
            f"Q{group['number_from']}: "
            "unexpected summary heading"
        )

    body = (
        f"**{heading}**\n\n"
        + body[len(heading):].strip()
    )

    if kind == "SUMMARY_WORD_BANK":
        body = body.replace(
            "{{gap:31}} However",
            "{{gap:31}}. However",
        )

    first = group["number_from"]
    last = group["number_to"]

    found = [
        int(number)
        for number in GAP.findall(body)
    ]

    if found != list(range(first, last + 1)):
        raise ValueError(
            f"Q{first}-{last}: "
            f"invalid gap tokens: {found}"
        )

    return instructions, body


def prepare(raw):
    if (
        raw.get("test")
        != "Cambridge 21 Reading Test 1"
        or len(raw.get("passages", [])) != 3
    ):
        raise ValueError(
            "Unexpected source dataset"
        )

    prepared = []
    numbers = []

    for passage in raw["passages"][1:]:
        number = passage["number"]

        if number not in (2, 3):
            raise ValueError(
                "Unexpected passage order"
            )

        groups = []

        for group in passage["question_groups"]:
            kind = group["question_type_hint"]

            if kind not in TYPES:
                raise ValueError(
                    f"Unsupported group type: {kind}"
                )

            instructions, content = group_content(
                group
            )

            options = (
                group.get("shared_options") or None
            )

            if kind == "YES_NO_NOT_GIVEN":
                # Fixed options are supplied by the site.
                options = None

            if kind in {
                "MATCHING_INFORMATION",
                "MATCHING_FEATURES",
            }:
                rules = {
                    "allow_option_reuse": True
                }
            else:
                rules = {}

            questions = []

            for index, question in enumerate(
                group["questions"], 1
            ):
                n = question["number"]

                if (
                    n != group["number_from"]
                    + index - 1
                ):
                    raise ValueError(
                        f"Q{n}: invalid question order"
                    )

                numbers.append(n)

                answer_format = (
                    question["answer_format"].lower()
                )

                if answer_format not in {
                    "text",
                    "choice",
                }:
                    raise ValueError(
                        f"Q{n}: invalid answer format"
                    )

                own_options = (
                    question.get("options") or None
                )

                if (
                    kind != "MULTIPLE_CHOICE"
                    and own_options
                ):
                    raise ValueError(
                        f"Q{n}: only MCQ may "
                        "have question-level options"
                    )

                effective = own_options or options

                if kind == "YES_NO_NOT_GIVEN":
                    effective = [
                        {"key": key, "text": key}
                        for key in (
                            "YES",
                            "NO",
                            "NOT_GIVEN",
                        )
                    ]

                answer = normalize_key(
                    TYPES[kind],
                    question["correct_answer"],
                )

                if answer_format == "choice":
                    valid_keys = {
                        item["key"]
                        for item in (effective or [])
                    }

                    if (
                        not valid_keys
                        or answer.get("choice")
                        not in valid_keys
                    ):
                        raise ValueError(
                            f"Q{n}: answer/options mismatch"
                        )

                elif not answer.get("accepted"):
                    raise ValueError(
                        f"Q{n}: missing text answer"
                    )

                config = (
                    question.get("config") or {}
                )

                if (
                    answer_format == "text"
                    and not isinstance(
                        config.get("max_words"), int
                    )
                ):
                    raise ValueError(
                        f"Q{n}: missing word limit"
                    )

                prompt = compact(
                    question.get("prompt")
                )

                if not prompt:
                    token = "{{gap:" + str(n) + "}}"

                    if (
                        not content
                        or content.count(token) != 1
                    ):
                        raise ValueError(
                            f"Q{n}: missing unique gap"
                        )

                    if answer_format == "text":
                        prompt = (
                            f"Enter the answer for "
                            f"blank {n}."
                        )
                    else:
                        prompt = (
                            f"Choose the letter "
                            f"for blank {n}."
                        )

                questions.append({
                    "number": n,
                    "position": index,
                    "question_type": TYPES[kind],
                    "answer_format": answer_format,
                    "prompt": prompt,
                    "options": (
                        own_options
                        if kind == "MULTIPLE_CHOICE"
                        else None
                    ),
                    "config": config,
                    "answer": answer,
                })

            groups.append({
                "title": (
                    f"Questions "
                    f"{group['number_from']}–"
                    f"{group['number_to']}"
                ),
                "instructions": (
                    instructions or None
                ),
                "content": content,
                "options": options,
                "rules": rules,
                "questions": questions,
            })

        prepared.append({
            "number": number,
            "title": passage["title"],
            "content": passage_body(passage),
            "groups": groups,
        })

    if numbers != list(range(14, 41)):
        raise ValueError(
            f"Expected questions 14–40, got {numbers}"
        )

    if [
        len(p["groups"])
        for p in prepared
    ] != [3, 3]:
        raise ValueError(
            "Expected three groups per new passage"
        )

    return prepared


def load_existing(cur, attempt_id):
    cur.execute(
        """
        SELECT
            tv.id AS version_id,
            tv.test_id,
            tv.version_number,
            tv.status,
            tv.band_table_id,
            t.title,
            t.type,
            t.visibility
        FROM public.attempts a
        JOIN public.test_versions tv
            ON tv.id = a.test_version_id
        JOIN public.tests t
            ON t.id = tv.test_id
        WHERE a.id = %s
        """,
        (attempt_id,),
    )

    info = cur.fetchone()

    if not info:
        raise RuntimeError(
            "Source attempt not found in database"
        )

    if (
        info["type"] != "reading"
        or info["status"] != "published"
    ):
        raise RuntimeError(
            "Attempt must belong to "
            "a published Reading version"
        )

    cur.execute(
        """
        SELECT id
        FROM public.sections
        WHERE test_version_id = %s
        ORDER BY position
        """,
        (info["version_id"],),
    )

    sections = cur.fetchall()

    if len(sections) != 1:
        raise RuntimeError(
            f"Source has {len(sections)} sections; "
            "expected exactly one"
        )

    cur.execute(
        """
        SELECT
            count(*) AS questions,
            coalesce(sum(q.max_score), 0) AS marks
        FROM public.questions q
        JOIN public.question_groups g
            ON g.id = q.question_group_id
        JOIN public.sections s
            ON s.id = g.section_id
        WHERE s.test_version_id = %s
        """,
        (info["version_id"],),
    )

    counts = cur.fetchone()

    if (
        counts["questions"] != 13
        or counts["marks"] != 13
    ):
        raise RuntimeError(
            f"Source is not exactly 13 marks: "
            f"{counts}"
        )

    return info, sections[0]["id"]


def copy_first_passage(
    cur,
    old_section_id,
    new_version_id,
):
    from psycopg.types.json import Jsonb

    cur.execute(
        """
        INSERT INTO public.sections
            (test_version_id, section_type, title,
             instructions, content, position,
             audio_object_key)
        SELECT
            %s, section_type, title, instructions,
            content, position, audio_object_key
        FROM public.sections
        WHERE id = %s
        RETURNING id
        """,
        (new_version_id, old_section_id),
    )

    new_section = cur.fetchone()["id"]

    cur.execute(
        """
        SELECT *
        FROM public.question_groups
        WHERE section_id = %s
        ORDER BY position
        """,
        (old_section_id,),
    )

    groups = cur.fetchall()

    if len(groups) != 2:
        raise RuntimeError(
            "Passage 1 must contain two groups"
        )

    copied = 0

    for group in groups:
        cur.execute(
            """
            INSERT INTO public.question_groups
                (section_id, title, instructions,
                 content, options, rules, position,
                 image_object_key)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                new_section,
                group["title"],
                group["instructions"],
                group["content"],
                (
                    Jsonb(group["options"])
                    if group["options"] is not None
                    else None
                ),
                Jsonb(group["rules"] or {}),
                group["position"],
                group["image_object_key"],
            ),
        )

        group_id = cur.fetchone()["id"]

        cur.execute(
            """
            SELECT *
            FROM public.questions
            WHERE question_group_id = %s
            ORDER BY position
            """,
            (group["id"],),
        )

        for question in cur.fetchall():
            cur.execute(
                """
                SELECT
                    correct_answer,
                    grading_config,
                    explanation
                FROM public.answer_keys
                WHERE question_id = %s
                """,
                (question["id"],),
            )

            answer = cur.fetchone()

            if not answer:
                raise RuntimeError(
                    "Existing question has no answer key"
                )

            correct = normalize_key(
                question["question_type"],
                answer["correct_answer"],
            )

            cur.execute(
                """
                INSERT INTO public.questions
                    (question_group_id,
                     question_type, prompt, config,
                     position, max_score,
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
                    Jsonb(question["config"] or {}),
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

            new_question = cur.fetchone()["id"]

            cur.execute(
                """
                INSERT INTO public.answer_keys
                    (question_id, correct_answer,
                     grading_config, explanation)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    new_question,
                    Jsonb(correct),
                    Jsonb(
                        answer["grading_config"] or {}
                    ),
                    answer["explanation"],
                ),
            )

            copied += 1

    if copied != 13:
        raise RuntimeError(
            "Passage 1 copy incomplete"
        )


def add_passages(cur, version_id, passages):
    from psycopg.types.json import Jsonb

    for passage in passages:
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
                passage["number"],
            ),
        )

        section_id = cur.fetchone()["id"]

        for group_position, group in enumerate(
            passage["groups"], 1
        ):
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
                    group_position,
                ),
            )

            group_id = cur.fetchone()["id"]

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
                         1, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        group_id,
                        question["question_type"],
                        question["prompt"],
                        Jsonb(question["config"]),
                        question["position"],
                        question["answer_format"],
                        (
                            Jsonb(question["options"])
                            if question["options"] is not None
                            else None
                        ),
                        question["number"],
                    ),
                )

                question_id = cur.fetchone()["id"]

                cur.execute(
                    """
                    INSERT INTO public.answer_keys
                        (question_id, correct_answer,
                         grading_config)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        question_id,
                        Jsonb(question["answer"]),
                        Jsonb({}),
                    ),
                )


def verify(cur, version_id):
    cur.execute(
        """
        SELECT
            count(distinct s.id) AS sections,
            count(distinct g.id) AS groups,
            count(distinct q.id) AS questions,
            count(ak.question_id) AS answer_keys,
            coalesce(sum(q.max_score), 0) AS marks
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

    actual = cur.fetchone()

    expected = {
        "sections": 3,
        "groups": 8,
        "questions": 40,
        "answer_keys": 40,
        "marks": 40,
    }

    for field, value in expected.items():
        if actual[field] != value:
            raise RuntimeError(
                f"Post-import verification failed: "
                f"{actual}"
            )

    cur.execute(
        """
        SELECT
            coalesce(
                q.display_number,
                row_number() OVER (
                    ORDER BY
                        s.position,
                        g.position,
                        q.position
                )
            ) AS n
        FROM public.questions q
        JOIN public.question_groups g
            ON g.id = q.question_group_id
        JOIN public.sections s
            ON s.id = g.section_id
        WHERE s.test_version_id = %s
        ORDER BY
            s.position,
            g.position,
            q.position
        """,
        (version_id,),
    )

    numbers = [
        int(row["n"])
        for row in cur.fetchall()
    ]

    if numbers != list(range(1, 41)):
        raise RuntimeError(
            f"Question numbering mismatch: {numbers}"
        )

    return actual


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--source",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--attempt-id",
        required=True,
    )

    parser.add_argument(
        "--title",
        default=(
            "Cambridge IELTS 21 - Test 1 "
            "- Full Reading [QA]"
        ),
    )

    parser.add_argument(
        "--probe",
        action="store_true",
        help="Read database without writing",
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create a separate hidden Draft",
    )

    parser.add_argument(
        "--confirm-create-draft",
        action="store_true",
    )

    args = parser.parse_args()

    if args.probe and args.apply:
        parser.error(
            "Choose --probe OR --apply"
        )

    if (
        args.apply
        and not args.confirm_create_draft
    ):
        parser.error(
            "--apply requires "
            "--confirm-create-draft"
        )

    source_bytes = args.source.read_bytes()
    digest = hashlib.sha256(
        source_bytes
    ).hexdigest()

    dataset = json.loads(
        source_bytes.decode("utf-8-sig")
    )

    passages = prepare(dataset)

    print("SOURCE SHA256:", digest)

    print(
        "Source Passages 2-3: "
        "2 sections / 6 groups / "
        "27 questions / 27 marks"
    )

    print(
        "Passage 1: copied from existing "
        "database version / 13 marks"
    )

    if not (args.probe or args.apply):
        print(
            "DRY RUN PASS - "
            "no database connection or writes"
        )
        return

    if not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "DATABASE_URL not set"
        )

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        os.environ["DATABASE_URL"],
        sslmode="require",
        connect_timeout=20,
        row_factory=dict_row,
    ) as conn:

        print(
            "Database target:",
            conn.info.host,
            "/",
            conn.info.dbname,
        )

        if args.apply:
            confirmation = input(
                "Confirm DEV database. "
                "Type CREATE DEV DRAFT: "
            )

            if confirmation != "CREATE DEV DRAFT":
                raise RuntimeError(
                    "Cancelled without database writes"
                )

        with conn.cursor() as cur:

            if args.apply:
                cur.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtext(%s)
                    )
                    """,
                    (args.title,),
                )

            info, old_section_id = load_existing(
                cur,
                args.attempt_id,
            )

            print(
                "Original test:",
                info["title"],
            )

            print(
                "Original published version:",
                info["version_id"],
            )

            if args.probe:
                print(
                    "READ-ONLY PROBE PASS"
                )
                conn.rollback()
                return

            if len(args.title.strip()) < 10:
                raise RuntimeError(
                    "Invalid QA test title"
                )

            cur.execute(
                """
                SELECT id
                FROM public.tests
                WHERE title = %s
                """,
                (args.title,),
            )

            if cur.fetchone():
                raise RuntimeError(
                    "QA test already exists. "
                    "Duplicate creation refused."
                )

            cur.execute(
                """
                INSERT INTO public.tests
                    (title, description,
                     type, visibility, access_type)
                VALUES
                    (%s, %s, 'reading',
                     'hidden', 'public')
                RETURNING id
                """,
                (
                    args.title,
                    "Cambridge 21 Reading Test 1 "
                    "- complete three-passage QA copy",
                ),
            )

            new_test_id = cur.fetchone()["id"]

            cur.execute(
                """
                INSERT INTO public.test_versions
                    (test_id, version_number,
                     status, band_table_id)
                VALUES
                    (%s, 1, 'draft', %s)
                RETURNING id
                """,
                (
                    new_test_id,
                    info["band_table_id"],
                ),
            )

            version_id = cur.fetchone()["id"]

            cur.execute(
                """
                INSERT INTO public.test_version_modes
                    (test_version_id,
                     mode, enabled,
                     time_limit_seconds, max_attempts,
                     answer_visibility, allow_pause,
                     allow_replay, allow_seek,
                     max_plays)
                SELECT
                    %s,
                    mode, enabled,
                    time_limit_seconds, max_attempts,
                    answer_visibility, allow_pause,
                    allow_replay, allow_seek,
                    max_plays
                FROM public.test_version_modes
                WHERE test_version_id = %s
                """,
                (
                    version_id,
                    info["version_id"],
                ),
            )

            copy_first_passage(
                cur,
                old_section_id,
                version_id,
            )

            add_passages(
                cur,
                version_id,
                passages,
            )

            counts = verify(
                cur,
                version_id,
            )

            print("DATABASE VERIFIED:", counts)
            print("NEW TEST ID:", new_test_id)
            print("NEW DRAFT VERSION ID:", version_id)
            print(
                "Original published test "
                "and its attempts remain unchanged."
            )

    print(
        "COMMITTED - HIDDEN QA DRAFT CREATED"
    )
    print(
        "NOT PUBLISHED - STUDENTS CANNOT SEE IT YET"
    )


if __name__ == "__main__":
    main()
