
"""Stage 5: Universal IELTS Reading Content QA.

Read-only by default.
--write creates local Stage 5 review reports.
Never approves content or writes to the database.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


INLINE = {
    "NOTE_COMPLETION",
    "SUMMARY_COMPLETION",
    "SUMMARY_WORD_BANK",
    "TABLE_COMPLETION",
    "FLOW_CHART_COMPLETION",
    "FORM_COMPLETION",
    "SENTENCE_COMPLETION",
}

TEXT = INLINE - {"SUMMARY_WORD_BANK"}

FIXED = {
    "TRUE_FALSE_NOT_GIVEN": [
        "TRUE", "FALSE", "NOT GIVEN"
    ],
    "YES_NO_NOT_GIVEN": [
        "YES", "NO", "NOT GIVEN"
    ],
}


def load(path):
    if not path.is_file():
        raise FileNotFoundError(path)

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def digest(path):
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1048576),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def compact(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()


def expected_bank(raw):
    match = re.search(
        r"\bA\s*[-–—]\s*([A-J])\b",
        raw,
        re.I,
    )

    if not match:
        return None

    last = match.group(1).upper()

    return list(
        "ABCDEFGHIJ"[:ord(last) - 64]
    )


def run(workspace):
    out = workspace / "output"

    paths = {
        "manifest": out / "stage1/manifest.json",
        "structure": out / "stage2/structure.json",
        "candidates": (
            out / "stage3/question_candidates_v2.json"
        ),
        "mapped": out / "stage4/reading_mapped.json",
        "probe": out / "stage4/answer_probe.json",
    }

    src = {
        name: load(path)
        for name, path in paths.items()
    }

    manifest = src["manifest"]
    structure = src["structure"]
    data = src["mapped"]
    probe = src["probe"]

    errors = []
    warnings = []
    review_items = []

    test = manifest.get("test_number")
    answer_page = manifest.get("answer_page")

    passages = structure.get("passages") or []
    groups = data.get("groups") or []

    candidate_groups = (
        src["candidates"].get("groups") or []
    )

    probe_answers = probe.get("answers") or {}

    mapped_slots = []
    marks = 0

    review = [
        "# Reading — Stage 5 Content Review",
        "",
        "Extracted draft. NOT an approved dataset.",
        "",
        f"Test: {test}",
        f"Answer-key PDF page: {answer_page}",
        "",
    ]

    # ----------------------------------
    # SOURCE INTEGRITY
    # ----------------------------------

    if not isinstance(test, int) or test < 1:
        errors.append(
            "Invalid test_number in manifest"
        )

    if (
        not isinstance(answer_page, int)
        or answer_page < 1
    ):
        errors.append(
            "Invalid answer_page in manifest"
        )

    if (
        len(passages) != 3
        or [
            p.get("passage_number")
            for p in passages
        ] != [1, 2, 3]
    ):
        errors.append(
            "Expected Passage 1, 2 and 3"
        )

    if not manifest.get("reading_pages"):
        errors.append(
            "Missing reading_pages in manifest"
        )

    if not data.get("answers_mapped"):
        errors.append(
            "Stage 4 answers_mapped is false"
        )

    if (
        data.get("answer_source_pdf_page")
        != answer_page
    ):
        errors.append(
            "Answer-key source page mismatch"
        )

    if probe.get("answer_page") != answer_page:
        errors.append(
            "Answer probe page mismatch"
        )

    if (
        probe.get("count") != 40
        or set(probe_answers) != {
            str(n) for n in range(1, 41)
        }
    ):
        errors.append(
            "Answer probe must contain Q1-Q40"
        )

    if len(candidate_groups) != len(groups):
        errors.append(
            "Stage 3/4 group count mismatch"
        )

    # ----------------------------------
    # PASSAGE CONTENT
    # ----------------------------------

    for passage in passages:
        number = passage.get("passage_number")

        body = "\n".join(
            chunk.get("raw_text", "")
            for chunk in passage.get(
                "body_chunks", []
            )
        )

        review.extend([
            f"## Passage {number}",
            "",
            f"PDF pages: {passage.get('source_pages')}",
            "",
            "### Extracted passage",
            "",
            body or "[MISSING]",
            "",
        ])

        if not compact(body):
            errors.append(
                f"Passage {number}: missing body"
            )

        elif len(compact(body)) < 1500:
            warnings.append(
                f"Passage {number}: "
                "suspiciously short passage body"
            )

        if re.search(
            r"(?m)^\s*READING\s+PASSAGE\s+[123]\s*$",
            body,
            re.I,
        ):
            review_items.append(
                f"Passage {number}: "
                "review heading/footer contamination"
            )

    source_groups = [
        group
        for passage in passages
        for group in passage.get(
            "question_groups", []
        )
    ]

    if len(source_groups) != len(groups):
        errors.append(
            "Stage 2/4 group count mismatch"
        )

    # ----------------------------------
    # QUESTION GROUPS
    # ----------------------------------

    for position, group in enumerate(groups):
        first = group.get("number_from")
        last = group.get("number_to")

        if (
            not isinstance(first, int)
            or not isinstance(last, int)
            or not 1 <= first <= last <= 40
        ):
            errors.append(
                f"Group {position}: invalid range"
            )
            continue

        tag = f"Q{first}-{last}"
        kind = group.get("question_type_hint")

        raw = group.get("raw_group_text") or ""
        template = group.get("render_template") or ""

        questions = group.get("questions") or []
        shared = group.get("shared_options") or []

        group_warnings = group.get("warnings") or []

        source = (
            source_groups[position]
            if position < len(source_groups)
            else None
        )

        candidate = (
            candidate_groups[position]
            if position < len(candidate_groups)
            else None
        )

        if source and (
            first, last
        ) != (
            source.get("number_from"),
            source.get("number_to"),
        ):
            errors.append(
                f"{tag}: Stage 2 range mismatch"
            )

        if candidate and (
            first, last
        ) != (
            candidate.get("number_from"),
            candidate.get("number_to"),
        ):
            errors.append(
                f"{tag}: Stage 3 range mismatch"
            )

        needs_review = (
            bool(group_warnings)
            or any(
                q.get("status") == "NEEDS_REVIEW"
                for q in questions
            )
        )

        if needs_review:
            review_items.append(
                f"{tag}: verify prompts and options "
                "against source PDF"
            )

        review.extend([
            f"## {tag} — {kind}",
            "",
            f"PDF pages: {group.get('source_pages')}",
            "",
        ])

        if template:
            review.extend([
                "### Inline template",
                "",
                template,
                "",
            ])

        if shared:
            review.extend([
                "### Shared options",
                "",
            ])

            for option in shared:
                review.append(
                    f"- {option.get('key')}: "
                    f"{option.get('text')}"
                )

            review.append("")

        if group_warnings:
            review.extend([
                "### Extraction warnings",
                "",
            ])

            for item in group_warnings:
                review.append(f"- {item}")

            review.append("")

        # ----------------------------------
        # INLINE GAP VERIFICATION
        # ----------------------------------

        if kind in INLINE:
            tokens = [
                int(n)
                for n in re.findall(
                    r"\{\{gap:(\d{1,2})\}\}",
                    template,
                )
            ]

            expected = list(
                range(first, last + 1)
            )

            if tokens != expected:
                errors.append(
                    f"{tag}: invalid gap sequence {tokens}"
                )

        # ----------------------------------
        # OPTION BANK VERIFICATION
        # ----------------------------------

        if kind in {
            "SUMMARY_WORD_BANK",
            "MATCHING_SENTENCE_ENDINGS",
        }:
            expected = expected_bank(raw)

            if expected is None:
                review_items.append(
                    f"{tag}: verify option-bank "
                    "range against PDF"
                )

            else:
                keys = [
                    o.get("key")
                    for o in shared
                ]

                if keys != expected:
                    errors.append(
                        f"{tag}: option keys {keys} "
                        f"expected {expected}"
                    )

        if "MISSING_PDF_OPTION_LABEL_I" in (
            " ".join(group_warnings)
        ):
            review_items.append(
                f"{tag}: visually verify reconstructed "
                "option I against PDF"
            )

        # ----------------------------------
        # INDIVIDUAL QUESTIONS
        # ----------------------------------

        for question in questions:
            n = question.get("number")

            slots = (
                question.get("numbers")
                or [n]
            )

            valid_slots = (
                isinstance(n, int)
                and isinstance(slots, list)
                and bool(slots)
                and all(
                    isinstance(x, int)
                    for x in slots
                )
                and slots == list(
                    range(slots[0], slots[-1] + 1)
                )
                and slots[0] == n
                and all(
                    first <= x <= last
                    for x in slots
                )
            )

            if not valid_slots:
                errors.append(
                    f"{tag}: invalid slots {slots}"
                )
                continue

            mapped_slots.extend(slots)

            score = question.get("max_score")

            if score is None:
                score = len(slots)

            if (
                type(score) is not int
                or score != len(slots)
            ):
                errors.append(
                    f"Q{n}: incorrect max_score"
                )
            else:
                marks += score

            status = question.get("status")

            if status not in {
                "EXTRACTED_CANDIDATE",
                "REVIEWED_CANDIDATE",
                "NEEDS_REVIEW",
            }:
                errors.append(
                    f"Q{n}: unexpected status {status}"
                )

            fmt = str(
                question.get("answer_format") or ""
            ).upper()

            answer = (
                question.get("correct_answer")
                or {}
            )

            options = (
                question.get("options")
                or shared
            )

            prompt = question.get("prompt") or ""

            # Check prompt or inline token.
            if kind in INLINE:
                token = f"{{{{gap:{n}}}}}"

                if template.count(token) != 1:
                    errors.append(
                        f"Q{n}: missing or duplicate gap"
                    )

            elif not compact(prompt):
                errors.append(
                    f"Q{n}: missing prompt"
                )

            # Cross-check the answer probe.
            key_raw = compact(
                probe_answers.get(str(n))
            )

            if not key_raw:
                errors.append(
                    f"Q{n}: missing raw answer"
                )

            # TEXT ANSWERS
            if kind in TEXT:
                accepted = (
                    answer.get("accepted") or []
                )

                if (
                    fmt != "TEXT"
                    or not isinstance(accepted, list)
                    or not accepted
                    or not all(
                        compact(x)
                        for x in accepted
                    )
                ):
                    errors.append(
                        f"Q{n}: invalid text answer"
                    )

                else:
                    optional = re.fullmatch(
                        r"(.+?)\s*\(([^()]+)\)",
                        key_raw,
                    )

                    if optional:
                        expected_text = {
                            optional.group(1).lower(),
                            (
                                optional.group(1)
                                + " "
                                + optional.group(2)
                            ).lower(),
                        }

                    elif (
                        " / " in key_raw
                        or " | " in key_raw
                    ):
                        expected_text = {
                            x.strip().lower()
                            for x in re.split(
                                r"\s+[/|]\s+",
                                key_raw,
                            )
                        }

                    else:
                        expected_text = {
                            key_raw.lower()
                        }

                    actual_text = {
                        compact(x).lower()
                        for x in accepted
                    }

                    if not expected_text <= actual_text:
                        errors.append(
                            f"Q{n}: text answer differs "
                            "from answer probe"
                        )

            # MULTI-ANSWER CHOICE
            elif len(slots) > 1:
                choices = (
                    answer.get("choices") or []
                )

                allowed = {
                    option.get("key")
                    for option in options
                }

                if (
                    fmt != "MULTI_CHOICE"
                    or len(choices) != len(slots)
                    or len(set(choices)) != len(choices)
                    or not set(choices) <= allowed
                ):
                    errors.append(
                        f"Q{n}: invalid multi-choice"
                    )

                probe_choices = sorted(
                    compact(
                        probe_answers.get(str(x))
                    ).upper()
                    for x in slots
                )

                if sorted(choices) != probe_choices:
                    errors.append(
                        f"Q{n}: multi-choice differs "
                        "from answer probe"
                    )

            # SINGLE CHOICE
            else:
                fixed = FIXED.get(kind)

                allowed = (
                    set(fixed)
                    if fixed
                    else {
                        o.get("key")
                        for o in options
                    }
                )

                choice = answer.get("choice")

                if (
                    fmt != "CHOICE"
                    or not allowed
                    or choice not in allowed
                ):
                    errors.append(
                        f"Q{n}: invalid choice answer"
                    )

                if choice != key_raw.upper():
                    errors.append(
                        f"Q{n}: choice differs "
                        "from answer probe"
                    )

            # Check option quality.
            if (
                kind not in FIXED
                and fmt in {
                    "CHOICE",
                    "MULTI_CHOICE",
                }
            ):
                keys = [
                    o.get("key")
                    for o in options
                ]

                if (
                    len(set(keys)) != len(keys)
                    or any(
                        not compact(o.get("text"))
                        for o in options
                    )
                ):
                    errors.append(
                        f"Q{n}: duplicate or empty options"
                    )

            review.extend([
                f"### Q{n}",
                "",
                f"Status: {status}",
                f"Format: {fmt}",
                "",
                "Prompt: "
                + (
                    compact(prompt)
                    or "[INLINE GAP]"
                ),
                "",
                "Answer: `"
                + json.dumps(
                    answer,
                    ensure_ascii=False,
                )
                + "`",
                "",
            ])

            if question.get("options"):
                for option in question["options"]:
                    review.append(
                        f"- {option.get('key')}: "
                        f"{option.get('text')}"
                    )

                review.append("")

            if re.search(
                r"(?mi)^\s*(?:Test\s*\d+|\d{2,3})\s*$",
                prompt + "\n" + template,
            ):
                review_items.append(
                    f"{tag}: possible PDF footer "
                    "contamination"
                )

    # ----------------------------------
    # GLOBAL VERIFICATION
    # ----------------------------------

    if mapped_slots != list(range(1, 41)):
        errors.append(
            f"Invalid question slots: {mapped_slots}"
        )

    if marks != 40:
        errors.append(
            f"Expected 40 marks, got {marks}"
        )

    if not data.get("manual_review_required"):
        errors.append(
            "Stage 4 manual_review_required is false"
        )

    for flag in data.get("review_flags") or []:
        if "parenthetical" in flag.lower():
            review_items.append(flag)

    review_items.append(
        "Verify all passage text, question wording, "
        "option banks and answer key against source PDF"
    )

    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    review_items = list(dict.fromkeys(review_items))

    report = {
        "status": (
            "BLOCKED"
            if errors
            else "CONTENT_REVIEW_REQUIRED"
        ),
        "test_number": test,
        "passages": len(passages),
        "groups": len(groups),
        "question_records": sum(
            len(g.get("questions") or [])
            for g in groups
        ),
        "question_slots": len(mapped_slots),
        "max_score": marks,
        "answer_key_pdf_page": answer_page,
        "stage4_review_flag_count": len(
            data.get("review_flags") or []
        ),
        "errors": errors,
        "warnings": warnings,
        "review_items": review_items,
        "requires_visual_approval": True,
        "import_ready": False,
        "source_sha256": {
            name: digest(path)
            for name, path in paths.items()
        },
    }

    review.extend([
        "## QA Results",
        "",
        f"Status: {report['status']}",
        "",
        "### Blocking errors",
        "",
    ])

    review.extend(
        f"- {error}"
        for error in errors
    )

    review.extend([
        "",
        "### Required PDF review",
        "",
    ])

    review.extend(
        f"- {item}"
        for item in review_items
    )

    review.append("")

    return report, "\n".join(review)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--workspace",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help="Write Stage 5 reports locally",
    )

    args = parser.parse_args()

    stage5 = (
        args.workspace / "output/stage5"
    )

    if args.write and stage5.exists():
        raise FileExistsError(
            "Stage 5 already exists; "
            f"refusing overwrite: {stage5}"
        )

    report, review_text = run(
        args.workspace
    )

    print(
        f"TEST {report['test_number']}: "
        f"PASSAGES={report['passages']} "
        f"GROUPS={report['groups']} "
        f"RECORDS={report['question_records']} "
        f"SLOTS={report['question_slots']} "
        f"MARKS={report['max_score']}"
    )

    print(
        f"ERRORS={len(report['errors'])}; "
        f"WARNINGS={len(report['warnings'])}; "
        f"PDF_REVIEW_ITEMS={len(report['review_items'])}"
    )

    for error in report["errors"]:
        print("ERROR:", error)

    for item in report["review_items"]:
        print("REVIEW:", item)

    if args.write:
        stage5.mkdir(
            parents=True,
            exist_ok=False,
        )

        (stage5 / "qa_report.json").write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        (stage5 / "qa_review.md").write_text(
            review_text,
            encoding="utf-8",
        )

        print(
            "REPORT:",
            stage5 / "qa_report.json",
        )

        print(
            "REVIEW:",
            stage5 / "qa_review.md",
        )

    else:
        print(
            "READ-ONLY PREFLIGHT; "
            "no files written"
        )

    print(
        "STATUS:",
        report["status"],
        "— NOT IMPORT READY — NO DB WRITES",
    )

    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
