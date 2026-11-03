
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

TITLE = "Cambridge IELTS 21 - Reading Test 1 FULL [QA]"

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

EXPECTED_GROUPS = [
    (1, 7), (8, 13),
    (14, 17), (18, 21), (22, 26),
    (27, 30), (31, 36), (37, 40),
]

GAP = re.compile(r"\{\{gap:(\d+)\}\}")


def compact(text):
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_key(value):
    return re.sub(
        r"\s+", "_", value.strip().upper()
    )


def normalize_options(options):
    if not options:
        return None

    result = []
    seen = set()

    for option in options:
        key = normalize_key(option["key"])
        label = compact(option["text"])

        if not key or not label or key in seen:
            raise ValueError("Invalid or duplicate option")

        seen.add(key)
        result.append({
            "key": key,
            "text": label,
        })

    return result


def prepare_passage(passage):
    number = passage["number"]
    title = passage["title"]
    content = passage["content"].strip()

    if number == 1:
        content = re.sub(
            r"^\s*The Davies Sisters\s*",
            "",
            content,
            count=1,
        )

        # Confirmed OCR correction from Test 1.
        content = content.replace(
            "we lmow also", "we know also"
        )

    elif number == 2:
        content = re.sub(
            r"^\s*Why we need silence\s*",
            "",
            content,
            count=1,
        )

        # Preserve the seven paragraph labels.
        content = re.sub(
            r"(?m)^[ \t]*([A-G])[ \t]*$",
            r"\n\n**\1** ",
            content,
        )

        for letter in "ABCDEFG":
            if content.count(f"**{letter}**") != 1:
                raise ValueError(
                    f"Passage 2: invalid section {letter}"
                )

    elif number == 3:
        content = re.sub(
            r"^\s*Book review:\s*"
            r"The World of Sugar by\s+"
            r"Ulbe Bosma\s*",
            "",
            content,
            count=1,
        )

        if not content.startswith(
            "Ulbe Bosma's The World of Sugar"
        ):
            raise ValueError(
                "Passage 3: unexpected opening"
            )

    else:
        raise ValueError(
            f"Unexpected passage number: {number}"
        )

    if len(compact(content)) < 1500:
        raise ValueError(
            f"Passage {number}: suspiciously short"
        )

    if "we lmow" in content or "UlbeBosma" in content:
        raise ValueError(
            f"Passage {number}: unresolved OCR artifact"
        )

    return {
        "number": number,
        "title": title,
        "content": content.strip(),
    }


def prepare_group(group):
    kind = group["question_type_hint"]

    if kind not in TYPE_MAP:
        raise ValueError(
            f"Unsupported type: {kind}"
        )

    first = group["number_from"]
    last = group["number_to"]

    shared = normalize_options(
        group.get("shared_options")
    )

    instructions = compact(
        group.get("instructions")
    )
    content = None

    if kind in INLINE_TYPES:
        template = group.get("render_template") or ""

        lines = template.splitlines()

        header_count = (
            2 if kind == "SUMMARY_WORD_BANK" else 3
        )

        if len(lines) <= header_count:
            raise ValueError(
                f"Q{first}-{last}: missing template"
            )

        instructions = compact(
            " ".join(lines[:header_count])
        )

        content = "\n".join(
            lines[header_count:]
        ).strip()

        if kind == "SUMMARY_WORD_BANK":
            content = re.sub(
                r"(\{\{gap:31\}\})\s+However",
                r"\1. However",
                content,
            )

        found = [
            int(value)
            for value in GAP.findall(content)
        ]

        expected = list(range(first, last + 1))

        if sorted(found) != expected:
            raise ValueError(
                f"Q{first}-{last}: invalid gaps {found}"
            )

    questions = []

    for position, question in enumerate(
        group["questions"], 1
    ):
        number = question["number"]

        if number != first + position - 1:
            raise ValueError(
                f"Q{number}: incorrect ordering"
            )

        answer_format = (
            question["answer_format"].lower()
        )

        if answer_format not in ("text", "choice"):
            raise ValueError(
                f"Q{number}: unsupported answer format"
            )

        own_options = normalize_options(
            question.get("options")
        )

        if (
            kind != "MULTIPLE_CHOICE"
            and own_options is not None
        ):
            raise ValueError(
                f"Q{number}: unexpected question options"
            )

        effective = own_options or shared

        if answer_format == "choice":
            if not effective:
                raise ValueError(
                    f"Q{number}: missing options"
                )

        answer = dict(
            question.get("correct_answer") or {}
        )

        if answer_format == "choice":
            key = normalize_key(
                answer.get("choice", "")
            )

            valid = {
                option["key"]
                for option in effective
            }

            if key not in valid:
                raise ValueError(
                    f"Q{number}: answer/options mismatch"
                )

            answer = {"choice": key}

        else:
            accepted = answer.get("accepted")

            if (
                not isinstance(accepted, list)
                or not accepted
                or not all(
                    isinstance(value, str) and value.strip()
                    for value in accepted
                )
            ):
                raise ValueError(
                    f"Q{number}: missing text answer"
                )

            answer = {
                "accepted": accepted
            }

        config = dict(
            question.get("config") or {}
        )

        if answer_format == "text":
            limit = config.get("max_words")

            if (
                type(limit) is not int
                or not 1 <= limit <= 10
            ):
                raise ValueError(
                    f"Q{number}: invalid word limit"
                )

        prompt = compact(
            question.get("prompt")
        )

        if kind in INLINE_TYPES:
            token = f"{{{{gap:{number}}}}}"

            if not content or content.count(token) != 1:
                raise ValueError(
                    f"Q{number}: missing unique gap"
                )

            if not prompt:
                prompt = (
                    f"Complete blank {number} "
                    "in the text above."
                )

        elif not prompt:
            raise ValueError(
                f"Q{number}: missing prompt"
            )

        questions.append({
            "number": number,
            "position": position,
            "question_type": TYPE_MAP[kind],
            "answer_format": answer_format,
            "prompt": prompt,
            "config": config,
            "options": own_options,
            "correct_answer": answer,
        })

    rules = {}

    if kind in {
        "MATCHING_INFORMATION",
        "MATCHING_FEATURES",
    }:
        rules["allow_option_reuse"] = True

    return {
        "title": f"Questions {first}-{last}",
        "instructions": instructions or None,
        "content": content,
        "options": shared,
        "rules": rules,
        "questions": questions,
    }


def prepare_dataset(source):
    raw_bytes = source.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()

    data = json.loads(
        raw_bytes.decode("utf-8-sig")
    )

    valid_names = {
        "Cambridge 21 Reading Test 1",
        "Cambridge IELTS 21 - Reading Test 1",
    }

    if data.get("test") not in valid_names:
        raise ValueError(
            "Incorrect source test"
        )

    passages = data.get("passages") or []

    if len(passages) != 3:
        raise ValueError(
            "Expected exactly three passages"
        )

    prepared = []
    ranges = []
    numbers = []

    for expected_number, passage in enumerate(
        passages, 1
    ):
        if passage["number"] != expected_number:
            raise ValueError(
                "Incorrect passage order"
            )

        result = prepare_passage(passage)

        groups = []

        for group in passage["question_groups"]:
            ranges.append((
                group["number_from"],
                group["number_to"],
            ))

            prepared_group = prepare_group(group)

            numbers.extend(
                q["number"]
                for q in prepared_group["questions"]
            )

            groups.append(prepared_group)

        result["groups"] = groups
        prepared.append(result)

    if ranges != EXPECTED_GROUPS:
        raise ValueError(
            f"Unexpected group ranges: {ranges}"
        )

    if numbers != list(range(1, 41)):
        raise ValueError(
            "Question sequence must be 1–40"
        )

    if [len(p["groups"]) for p in prepared] != [
        2, 3, 3
    ]:
        raise ValueError(
            "Unexpected passage group counts"
        )

    return prepared, digest, data.get("status")


def insert_draft(passages, url):
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    with psycopg.connect(
        url,
        sslmode="require",
        connect_timeout=20,
        prepare_threshold=None,
        row_factory=dict_row,
    ) as conn:

        print(
            "Database:",
            conn.info.host,
            "/",
            conn.info.dbname,
        )

        confirmation = input(
            "Type CREATE HIDDEN QA DRAFT: "
        )

        if confirmation != "CREATE HIDDEN QA DRAFT":
            raise RuntimeError(
                "Cancelled without database writes"
            )

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtext(%s)
                )
                """,
                (TITLE,),
            )

            cur.execute(
                """
                SELECT id
                FROM public.tests
                WHERE title = %s
                """,
                (TITLE,),
            )

            if cur.fetchone():
                raise RuntimeError(
                    "QA test already exists. "
                    "No duplicate created."
                )

            cur.execute(
                """
                INSERT INTO public.tests
                    (title, description, type,
                     visibility, access_type)
                VALUES
                    (%s, %s, 'reading',
                     'hidden', 'public')
                RETURNING id
                """,
                (
                    TITLE,
                    "Complete three-passage "
                    "Cambridge 21 Reading QA draft",
                ),
            )

            test_id = cur.fetchone()["id"]

            cur.execute(
                """
                INSERT INTO public.test_versions
                    (test_id, version_number, status)
                VALUES (%s, 1, 'draft')
                RETURNING id
                """,
                (test_id,),
            )

            version_id = cur.fetchone()["id"]

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
                            (section_id, title,
                             instructions, content,
                             options, rules, position)
                        VALUES
                            (%s, %s, %s, %s,
                             %s, %s, %s)
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
                                 config, position,
                                 max_score, answer_format,
                                 options, display_number)
                            VALUES
                                (%s, %s, %s, %s,
                                 %s, 1, %s, %s, %s)
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
                                (question_id,
                                 correct_answer,
                                 grading_config)
                            VALUES (%s, %s, %s)
                            """,
                            (
                                question_id,
                                Jsonb(
                                    question["correct_answer"]
                                ),
                                Jsonb({}),
                            ),
                        )

            cur.execute(
                """
                SELECT
                    COUNT(DISTINCT s.id) AS passages,
                    COUNT(DISTINCT g.id) AS groups,
                    COUNT(DISTINCT q.id) AS questions,
                    COUNT(ak.question_id) AS answer_keys,
                    COALESCE(SUM(q.max_score), 0) AS marks,
                    ARRAY_AGG(
                        q.display_number
                        ORDER BY
                            s.position,
                            g.position,
                            q.position
                    ) AS numbers
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

            result = cur.fetchone()

            if (
                result["passages"] != 3
                or result["groups"] != 8
                or result["questions"] != 40
                or result["answer_keys"] != 40
                or result["marks"] != 40
                or result["numbers"] != list(range(1, 41))
            ):
                raise RuntimeError(
                    f"Database verification failed: {result}"
                )

            print("DATABASE VERIFICATION PASS")
            print("Test ID:", test_id)
            print("Version ID:", version_id)
            print("Status: draft")
            print("Visibility: hidden")
            print("Passages: 3")
            print("Groups: 8")
            print("Questions: 40")
            print("Marks: 40")

    print("TRANSACTION COMMITTED")
    print("Original Test 1 remains unchanged.")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--source",
        required=True,
        type=Path,
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

    passages, digest, status = prepare_dataset(
        args.source
    )

    print("\n=== CAMBRIDGE 21 TEST 1 FULL ===")
    print("Title:", TITLE)
    print("Source:", args.source)
    print("SHA256:", digest)
    print("Source status:", status)
    print("Passages:", len(passages))
    print("Groups:", sum(
        len(p["groups"]) for p in passages
    ))
    print("Questions:", sum(
        len(g["questions"])
        for p in passages
        for g in p["groups"]
    ))
    print("Marks: 40")

    if not args.apply:
        print(
            "DRY RUN PASS - NO DATABASE WRITES"
        )
        return

    if not args.confirm_create_draft:
        parser.error(
            "--apply requires "
            "--confirm-create-draft"
        )

    url = os.getenv("DATABASE_URL")

    if not url:
        raise RuntimeError(
            "DATABASE_URL is not configured"
        )

    insert_draft(passages, url)


if __name__ == "__main__":
    main()
