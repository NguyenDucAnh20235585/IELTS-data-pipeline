
"""Finalize IELTS Reading data from reviewed questions and Answer IR.

Produces a preview dataset and a content verification report.
Never writes to the database or grants import approval.
"""

import copy
import json
import re
from pathlib import Path


STRUCTURE = Path("output/stage2/structure.json")
REVIEWED = Path("output/stage3/question_candidates_reviewed.json")
ANSWER_IR = Path("output/stage4/answer_ir.json")
QA = Path("output/stage5/qa_report.json")
DEST = Path("output/stage6/reading_test1_demo.json")

# Populated by the current runner when available.
TEST_TITLE = "IELTS Reading Test"
TITLES = {}
DISPLAY_TITLES = {}
PASSAGE_GLOSSARY = {}
EXPECTED_GROUP_COUNT = None
ANSWER_PAGE = None


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def compact(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def numbered(mapping, number, default=None):
    return mapping.get(
        number,
        mapping.get(str(number), default),
    )


def unique(values):
    return list(dict.fromkeys(values))


def strip_page_tail(text, pdf_page):
    """Remove recognizable PDF footer lines, not body text."""
    lines = text.rstrip().splitlines()

    while lines:
        tail = lines[-1].strip()
        printed_page = str(pdf_page - 1)

        is_page_number = tail == printed_page

        is_footer = bool(
            re.match(r"^[➔→]", tail)
            and re.search(
                r"\bp\.?\s*\d+", tail, re.I
            )
        )

        if is_page_number or is_footer:
            lines.pop()
        else:
            break

    return "\n".join(lines).strip()


def detect_acronyms(text):
    """Extract abbreviations confirmed by preceding words."""
    result = set()

    for match in re.finditer(
        r"\(([A-Z]{2,6})\)", text
    ):
        abbreviation = match.group(1)

        preceding = text[
            max(0, match.start() - 120):match.start()
        ]

        words = re.findall(
            r"[A-Za-z]+", preceding
        )

        length = len(abbreviation)

        if len(words) < length:
            continue

        initials = "".join(
            word[0].upper()
            for word in words[-length:]
        )

        if initials == abbreviation:
            result.add(abbreviation)

    return result


def repair_confusable_glyphs(
    text, acronyms, passage_text, location, corrections
):
    """Repair glyph ambiguity only with passage-level evidence."""
    if not text:
        return text

    for abbreviation in sorted(acronyms):
        if not abbreviation.endswith("I"):
            continue

        suspect = abbreviation[:-1] + "l"
        pattern = rf"\b{re.escape(suspect)}\b"

        # Preserve genuine names or terms already occurring
        # in the passage with that exact spelling.
        if re.search(pattern, passage_text):
            continue

        count = len(re.findall(pattern, text))

        if not count:
            continue

        text = re.sub(
            pattern, abbreviation, text
        )

        corrections.append({
            "location": location,
            "operation": "ocr_confusable_glyph",
            "before": suspect,
            "after": abbreviation,
            "count": count,
            "evidence": "expanded acronym in passage",
            "requires_visual_confirmation": False,
        })

    return text


def restore_source_spacing(value, source):
    """Restore whitespace from a unique raw-source match."""
    value = compact(value)

    if not value or not source:
        return value

    positions = [
        index
        for index, character in enumerate(source)
        if not character.isspace()
    ]

    flat_source = "".join(
        source[index] for index in positions
    )

    flat_value = "".join(
        character
        for character in value
        if not character.isspace()
    )

    if not flat_value:
        return value

    start = flat_source.find(flat_value)

    if start < 0:
        return value

    if flat_source.find(
        flat_value, start + 1
    ) >= 0:
        return value

    first = positions[start]
    last = positions[
        start + len(flat_value) - 1
    ] + 1

    return compact(source[first:last])


def restore_prompt_spacing(prompt, source):
    """Align sentences to the original extracted group."""
    sentences = re.split(
        r"(?<=[.!?])\s+(?=[A-Z])",
        compact(prompt),
    )

    return " ".join(
        restore_source_spacing(sentence, source)
        for sentence in sentences
    )


def passage_title(passage, findings):
    number = passage["passage_number"]
    configured = numbered(TITLES, number)
    display = numbered(DISPLAY_TITLES, number)

    if configured:
        return configured, display or configured

    raw = passage["body_chunks"][0]["raw_text"]
    lines = [
        line.strip()
        for line in raw.splitlines()
    ]

    for index, line in enumerate(lines):
        if re.search(
            r"Passage\s+\d+\s+below\.",
            line,
            re.I,
        ):
            for candidate in lines[index + 1:]:
                if candidate and not re.fullmatch(
                    r"\d+", candidate
                ):
                    findings.append(
                        f"Passage {number}: inferred title "
                        "requires visual verification"
                    )
                    return candidate, display or candidate

    raise ValueError(
        f"Passage {number}: title not identified"
    )



def prepare_passage(passage, findings):
    number = passage["passage_number"]

    source_title, title = passage_title(
        passage, findings
    )

    chunks = []

    for index, chunk in enumerate(
        passage.get("body_chunks", [])
    ):
        pdf_page = chunk["pdf_page"]

        text = strip_page_tail(
            chunk["raw_text"],
            pdf_page,
        )

        # Some multi-column PDFs place the printed
        # page number between text columns rather
        # than at the end of extracted page text.
        printed = str(pdf_page - 1)

        page_marker = re.compile(
            rf"(?m)^[ \t]*{re.escape(printed)}"
            r"[ \t]*$(?:\r?\n)?"
        )

        text, removed = page_marker.subn(
            "",
            text,
        )

        if removed > 1:
            raise ValueError(
                f"Passage {number}: multiple possible "
                f"page numbers on PDF page {pdf_page}"
            )

        if removed:
            findings.append(
                f"Passage {number}: removed embedded "
                f"printed page number {printed} from "
                f"PDF page {pdf_page}; verify layout"
            )

        if index > 0:
            text = re.sub(
                r"(?i)^\s*Reading\s*\n",
                "",
                text,
                count=1,
            )

        chunks.append(text)

    content = "\n".join(chunks).strip()

    match = re.search(
        re.escape(source_title),
        content,
        flags=re.I,
    )

    if not match:
        raise ValueError(
            f"Passage {number}: title missing in PDF body"
        )

    content = content[match.start():].strip()

    if len(compact(content)) < 1500:
        raise ValueError(
            f"Passage {number}: suspiciously short"
        )

    return {
        "number": number,
        "title": title,
        "content": content,
        "glossary": numbered(
            PASSAGE_GLOSSARY,
            number,
            [],
        ),
        "source_pages": passage["source_pages"],
        "question_groups": [],
    }

def split_inline_content(template):
    """Separate completion instructions from actual content."""
    lines = template.splitlines()
    instruction_lines = []

    patterns = (
        "complete ",
        "choose ",
        "write ",
        "answer ",
        "use ",
        "no more than ",
        "you may ",
        "in boxes ",
    )

    for line in lines:
        stripped = line.strip().lower()

        if not stripped and instruction_lines:
            instruction_lines.append(line)
        elif stripped.startswith(patterns):
            instruction_lines.append(line)
        else:
            break

    count = len(instruction_lines)

    instructions = (
        "\n".join(instruction_lines).strip()
        or None
    )

    content = "\n".join(
        lines[count:]
    ).strip()

    if not content:
        raise ValueError(
            "Missing completion content"
        )

    return instructions, content


def card_instructions(group, items):
    raw = group.get("raw_group_text") or ""
    first = group["number_from"]

    match = re.search(
        rf"(?m)^\s*{first}\s*(?:\n|$)",
        raw,
    )

    if match:
        return raw[:match.start()].strip() or None

    if any(
        item["answer_format"] == "multi_choice"
        for item in items
    ):
        lines = [
            line.strip()
            for line in raw.splitlines()[:2]
        ]

        if len(lines) == 2 and all(
            re.match(r"(?i)^(choose|write)\b", line)
            for line in lines
        ):
            return "\n".join(lines)

    return None


def normalize_inline_prose(
    content, first, last, corrections, findings
):
    """Normalize text wrapping, preserving answer blanks."""
    gap = r"\[\d{1,2}:\s*_+\]"

    if not compact(content):
        raise ValueError(
            f"Q{first}-{last}: empty completion"
        )

    # A separated period immediately before a lowercase
    # continuation is suspicious PDF extraction.
    orphan = re.compile(
        rf"({gap})[ \t]*\n"
        r"[ \t]*\.[ \t]*\n"
        r"[ \t]*(?=[a-z])"
    )

    count = len(orphan.findall(content))

    if count:
        content = orphan.sub(r"\1 ", content)

        corrections.append({
            "location": f"Q{first}-{last}",
            "operation": "remove_orphan_period",
            "count": count,
            "requires_visual_confirmation": True,
        })

        findings.append(
            f"Q{first}-{last}: removed punctuation "
            "requires PDF verification"
        )

    # A new sentence after a blank can indicate missing
    # punctuation in the text extraction.
    sentence_boundary = re.compile(
        rf"({gap})[ \t]*\n"
        r"[ \t]*(?=[A-Z][a-z]+\b)"
    )

    count = len(sentence_boundary.findall(content))

    if count:
        content = sentence_boundary.sub(
            r"\1. ",
            content,
        )

        corrections.append({
            "location": f"Q{first}-{last}",
            "operation": "restore_sentence_boundary",
            "count": count,
            "requires_visual_confirmation": True,
        })

        findings.append(
            f"Q{first}-{last}: restored punctuation "
            "requires PDF verification"
        )

    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip()
    ]

    heading = None

    if (
        len(lines) > 1
        and len(lines[0]) <= 100
        and not re.search(gap, lines[0])
        and not re.search(r"[.!?]$", lines[0])
    ):
        heading = lines.pop(0)

    body = " ".join(lines)

    body = re.sub(
        r"\s+([,.;:!?])",
        r"\1",
        body,
    )

    body = re.sub(
        r"([,.;:!?])(?=[A-Za-z])",
        r"\1 ",
        body,
    )

    body = re.sub(
        r"[ \t]{2,}", " ", body
    ).strip()

    if not body:
        raise ValueError(
            f"Q{first}-{last}: empty normalized body"
        )

    return (
        heading + "\n\n" + body
        if heading else body
    )


def group_source_texts(group):
    """Get source text from review and extracted PDF pages."""
    sources = []

    raw = group.get("raw_group_text") or ""
    if raw:
        sources.append(raw)

    for number in group.get("source_pages", []):
        path = Path(
            f"output/stage1/pages/page_{number:03d}.json"
        )

        if not path.is_file():
            continue

        page = load(path)
        text = page.get("text") or ""

        if text:
            sources.append(text)

    return sources



def clean_inline_header(content, group, corrections):
    """Remove a PDF running header, including when joined to body text.

    Requires evidence that Reading is a standalone header
    in the extracted source.
    """
    sources = group_source_texts(group)

    header_confirmed = any(
        re.search(
            r"(?im)^[ \t]*Reading[ \t]*$",
            source,
        )
        for source in sources
    )

    if not header_confirmed:
        return content

    lines = content.splitlines(keepends=True)
    removed = 0

    for index in range(min(len(lines), 6)):
        line = lines[index]

        # Case 1: header occupies its own line.
        if line.strip().lower() == "reading":
            lines[index] = ""
            removed = 1
            break

        # Case 2: extraction joined "Reading" with body.
        # Only check near the beginning of the content.
        match = re.match(
            r"^[ \t]*Reading[ \t]+(?=[A-Z][a-z])",
            line,
        )

        if match:
            lines[index] = line[match.end():]
            removed = 1
            break

    if removed:
        corrections.append({
            "location": (
                f"Q{group['number_from']}-"
                f"{group['number_to']}"
            ),
            "operation": "remove_running_header",
            "count": removed,
            "evidence": "standalone Reading header in PDF source",
            "requires_visual_confirmation": False,
        })

    return "".join(lines)


def repair_inline_spacing(content, group, corrections):
    """Repair merged words using local evidence from source text.

    A split is accepted only if:
    - the merged token is absent as a source word;
    - source contains the two separate adjacent words;
    - at least one neighboring word matches;
    - exactly one split is supported.

    Also ensures spaces around inline answer blanks.
    """
    sources = group_source_texts(group)
    word_pattern = re.compile(
        r"[A-Za-z]+(?:['’][A-Za-z]+)?"
    )
    gap_pattern = r"\[\d{1,2}:\s*_+\]"

    source_vocabulary = set()
    source_pairs = {}

    for source in sources:
        words = [
            match.group()
            for match in word_pattern.finditer(source)
        ]

        lowered = [
            word.lower()
            for word in words
        ]

        source_vocabulary.update(lowered)

        for index in range(len(words) - 1):
            left = words[index]
            right = words[index + 1]

            combined = (
                left + right
            ).lower()

            before = (
                lowered[index - 1]
                if index > 0 else None
            )

            after = (
                lowered[index + 2]
                if index + 2 < len(words)
                else None
            )

            source_pairs.setdefault(
                combined, []
            ).append({
                "replacement": left + " " + right,
                "before": before,
                "after": after,
            })

    body_matches = list(
        word_pattern.finditer(content)
    )

    replacements = {}
    split_count = 0

    for index, match in enumerate(body_matches):
        word = match.group()
        lowered = word.lower()

        if len(word) < 5:
            continue

        # Do not split a word already found intact
        # in the extracted PDF text.
        if lowered in source_vocabulary:
            continue

        candidates = source_pairs.get(
            lowered, []
        )

        if not candidates:
            continue

        previous_word = (
            body_matches[index - 1]
            .group().lower()
            if index > 0 else None
        )

        next_word = (
            body_matches[index + 1]
            .group().lower()
            if index + 1 < len(body_matches)
            else None
        )

        supported = set()

        for candidate in candidates:
            matches_before = (
                previous_word is not None
                and candidate["before"] == previous_word
            )

            matches_after = (
                next_word is not None
                and candidate["after"] == next_word
            )

            if matches_before or matches_after:
                supported.add(
                    candidate["replacement"]
                )

        if len(supported) != 1:
            continue

        replacement = supported.pop()

        if (
            "".join(replacement.split()).lower()
            != lowered
        ):
            continue

        replacements[match.start()] = (
            match.end(),
            replacement,
        )

        split_count += 1

    # Apply replacements without shifting source indices.
    chunks = []
    cursor = 0

    for start in sorted(replacements):
        end, replacement = replacements[start]

        chunks.append(content[cursor:start])
        chunks.append(replacement)

        cursor = end

    chunks.append(content[cursor:])
    content = "".join(chunks)

    # A gap is a separate answer token; it must not
    # touch an ordinary word without a space.
    content, before_count = re.subn(
        rf"(?<=[A-Za-z0-9])(?={gap_pattern})",
        " ",
        content,
    )

    content, after_count = re.subn(
        rf"({gap_pattern})(?=[A-Za-z0-9])",
        r"\1 ",
        content,
    )

    count = (
        split_count
        + before_count
        + after_count
    )

    if count:
        corrections.append({
            "location": (
                f"Q{group['number_from']}-"
                f"{group['number_to']}"
            ),
            "operation": "restore_inline_spacing",
            "count": count,
            "merged_words_repaired": split_count,
            "gap_boundaries_repaired": (
                before_count + after_count
            ),
            "evidence": (
                "adjacent source words with "
                "matching local context"
            ),
            "requires_visual_confirmation": False,
        })

    return content

def prepare_group(
    group, items, passage_context, corrections, findings
):
    first = group["number_from"]
    last = group["number_to"]
    expected = list(range(first, last + 1))

    question_map = {
        q["number"]: q
        for q in group["questions"]
    }

    if sorted(question_map) != expected:
        raise ValueError(
            f"Q{first}-{last}: source coverage invalid"
        )

    if group.get("warnings"):
        raise ValueError(
            f"Q{first}-{last}: unresolved review warnings"
        )

    source_pages = group["source_pages"]
    raw_source = group.get("raw_group_text") or ""

    template = strip_page_tail(
        (group.get("render_template") or "").strip(),
        source_pages[-1],
    )

    # A running page header is not question content.
    template = re.sub(
        r"(?i)^\s*Reading\s*\n",
        "",
        template,
        count=1,
    )

    inline = bool(template) and any(
        q.get("render_mode") == "INLINE_GAP"
        for q in question_map.values()
    )

    
    if inline:
        instructions, content = split_inline_content(
            template
        )

        for number in expected:
            token = f"{{{{gap:{number}}}}}"

            if template.count(token) != 1:
                raise ValueError(
                    f"Q{number}: invalid gap token"
                )

            content = content.replace(
                token,
                f"[{number}: ______]",
            )

        # Remove page header after ALL gaps are replaced.
        # This must be outside the for loop.
        content = clean_inline_header(
            content,
            group,
            corrections,
        )

        kind = group["question_type_hint"]

        if kind == "TABLE_COMPLETION":
            layout = "table"

            if not re.search(
                r"(?m)^\s*\|.*\|\s*$",
                content,
            ):
                findings.append(
                    f"Q{first}-{last}: table layout "
                    "requires reconstruction"
                )

        elif kind == "FLOW_CHART_COMPLETION":
            layout = "flow_chart"

            findings.append(
                f"Q{first}-{last}: flowchart requires preview"
            )

        elif kind == "DIAGRAM_LABEL_COMPLETION":
            layout = "diagram"

            findings.append(
                f"Q{first}-{last}: diagram requires preview"
            )

        else:
            layout = "inline"

            # Restore spacing BEFORE punctuation normalization.
            # The original PDF still provides better evidence
            # before sentence boundaries are changed.
            content = repair_inline_spacing(
                content,
                group,
                corrections,
            )

            content = normalize_inline_prose(
                content,
                first,
                last,
                corrections,
                findings,
            )

    else:
        layout = "question_cards"
        content = None
        instructions = card_instructions(
            group, items
        )

    acronyms = passage_context["acronyms"]
    passage_text = passage_context["text"]
    location = f"Q{first}-{last}"

    if content is not None:
        content = repair_confusable_glyphs(
            content,
            acronyms,
            passage_text,
            location + " content",
            corrections,
        )

    if instructions:
        instructions = repair_confusable_glyphs(
            instructions,
            acronyms,
            passage_text,
            location + " instructions",
            corrections,
        )

    shared = copy.deepcopy(
        group.get("shared_options") or []
    )

    for option in shared:
        option["text"] = restore_source_spacing(
            option["text"], raw_source
        )

        option["text"] = repair_confusable_glyphs(
            option["text"],
            acronyms,
            passage_text,
            location + " shared option",
            corrections,
        )

    prepared = []
    slot_order = []

    for position, item in enumerate(items, 1):
        numbers = item["numbers"]

        if (
            not numbers
            or numbers != list(
                range(numbers[0], numbers[0] + len(numbers))
            )
            or any(n not in question_map for n in numbers)
        ):
            raise ValueError(
                f"Q{first}-{last}: invalid IR numbers {numbers}"
            )

        slot_order.extend(numbers)
        first_question = question_map[numbers[0]]

        if all(
            question_map[n].get("render_mode")
            == "INLINE_GAP"
            for n in numbers
        ):
            if item["answer_format"] == "choice":
                prompt = (
                    f"Choose the answer for blank {numbers[0]}."
                )
            else:
                prompt = (
                    f"Enter the answer for blank {numbers[0]}."
                )
        else:
            prompt = compact(
                first_question.get("prompt")
            )

            prompt = restore_prompt_spacing(
                prompt, raw_source
            )

            if not prompt:
                raise ValueError(
                    f"Q{numbers}: missing prompt"
                )

        options = copy.deepcopy(
            first_question.get("options") or shared
        ) or None

        for option in options or []:
            option["text"] = restore_source_spacing(
                option["text"], raw_source
            )

            option["text"] = repair_confusable_glyphs(
                option["text"],
                acronyms,
                passage_text,
                f"Q{numbers[0]} option {option['key']}",
                corrections,
            )

        prompt = repair_confusable_glyphs(
            prompt,
            acronyms,
            passage_text,
            f"Q{numbers[0]} prompt",
            corrections,
        )

        # Avoid duplicating group instructions in a
        # shared multi-choice question's prompt.
        if instructions:
            prefix = compact(instructions)

            if prompt.lower().startswith(prefix.lower()):
                prompt = prompt[len(prefix):].strip()

                if not prompt:
                    raise ValueError(
                        f"Q{numbers}: empty prompt "
                        "after instruction cleanup"
                    )

        answer_format = item["answer_format"]
        correct_answer = item["correct_answer"]

        if answer_format in ("choice", "multi_choice"):
            keys = {
                option["key"]
                for option in (options or [])
            }

            if answer_format == "choice":
                choices = [
                    correct_answer.get("choice")
                ]
            else:
                choices = correct_answer.get(
                    "choices", []
                )

            if (
                not keys
                or not set(choices).issubset(keys)
            ):
                raise ValueError(
                    f"Q{numbers}: invalid choice mapping"
                )

        elif answer_format == "text":
            if not correct_answer.get("accepted"):
                raise ValueError(
                    f"Q{numbers}: missing accepted answers"
                )

        else:
            raise ValueError(
                f"Q{numbers}: unsupported answer format"
            )

        prepared.append({
            "number": numbers[0],
            "numbers": numbers,
            "display_number": numbers[0],
            "position": position,
            "question_type": item["question_type"],
            "answer_format": answer_format,
            "max_score": item["max_score"],
            "prompt": prompt,
            "options": options,
            "config": copy.deepcopy(
                item.get("config") or {}
            ),
            "correct_answer": copy.deepcopy(
                correct_answer
            ),
            "grading_config": copy.deepcopy(
                item.get("grading_config") or {}
            ),
        })

    if slot_order != expected:
        raise ValueError(
            f"Q{first}-{last}: IR slots out of order"
        )

    # Recreate render_template from finalized content.
    # Never leave old OCR text in a second field.
    if inline:
        finalized_template = content

        for number in expected:
            finalized_template = finalized_template.replace(
                f"[{number}: ______]",
                f"{{{{gap:{number}}}}}",
            )

        if instructions:
            finalized_template = (
                instructions + "\n\n" + finalized_template
            )
    else:
        finalized_template = None

    return {
        "number_from": first,
        "number_to": last,
        "title": f"Questions {first}-{last}",
        "instructions": instructions,
        "content": content,
        "render_template": finalized_template,
        "render_mode": layout,
        "options": shared or None,
        "source_pages": source_pages,
        "questions": prepared,
    }


def recheck_qa_warnings(passages, old_warnings):
    """Check which old indicators survive in final output."""
    groups = {
        (g["number_from"], g["number_to"]): g
        for passage in passages
        for g in passage["question_groups"]
    }

    resolved = []
    unresolved = []

    for warning in old_warnings:
        match = re.match(
            r"Q(\d+)-(\d+):", warning
        )

        if not match:
            unresolved.append(warning)
            continue

        key = tuple(map(int, match.groups()))
        group = groups.get(key)

        if group is None:
            unresolved.append(warning)
            continue

        pieces = [
            group.get("instructions") or "",
            group.get("content") or "",
            group.get("render_template") or "",
        ]

        for option in group.get("options") or []:
            pieces.append(option.get("text") or "")

        for question in group["questions"]:
            pieces.append(question.get("prompt") or "")

            for option in question.get("options") or []:
                pieces.append(option.get("text") or "")

        visible = "\n".join(pieces)

        if "possible OCR/footer noise" in warning:
            remains = re.search(
                r"[➔→]|\bp\.?\s*120\b",
                visible,
                re.I,
            )

            if not remains:
                resolved.append(warning)
                continue

        if "possible OCR Al instead of AI" in warning:
            if not re.search(r"\bAl\b", visible):
                resolved.append(warning)
                continue

        unresolved.append(warning)

    return resolved, unresolved


def finalize():
    structure = load(STRUCTURE)
    reviewed = load(REVIEWED)
    ir = load(ANSWER_IR)
    qa = load(QA)

    if qa.get("errors") or qa.get("status") == "BLOCKED":
        raise ValueError(
            "Stage 5 QA is blocked"
        )

    if (
        ir.get("question_slots") != 40
        or ir.get("max_score") != 40
        or ir.get("contract_version") != 1
    ):
        raise ValueError(
            "Invalid Answer IR contract or score"
        )

    corrections = []
    findings = []

    passages = [
        prepare_passage(p, findings)
        for p in structure["passages"]
    ]

    if [p["number"] for p in passages] != [1, 2, 3]:
        raise ValueError(
            "Expected passages 1, 2, 3"
        )

    contexts = {
        passage["number"]: {
            "text": passage["content"],
            "acronyms": detect_acronyms(
                passage["content"]
            ),
        }
        for passage in passages
    }

    groups = reviewed["groups"]

    if (
        EXPECTED_GROUP_COUNT is not None
        and len(groups) != EXPECTED_GROUP_COUNT
    ):
        raise ValueError(
            "Reviewed group count mismatch"
        )

    source_ranges = [
        (g["number_from"], g["number_to"])
        for g in groups
    ]

    structure_ranges = [
        (g["number_from"], g["number_to"])
        for passage in structure["passages"]
        for g in passage["question_groups"]
    ]

    if source_ranges != structure_ranges:
        raise ValueError(
            "Stage 2 and Stage 3 group mismatch"
        )

    ir_by_group = {
        key: [] for key in source_ranges
    }

    for item in ir["answer_items"]:
        key = tuple(item["source_group"])

        if key not in ir_by_group:
            raise ValueError(
                f"Unknown Answer IR group: {key}"
            )

        ir_by_group[key].append(item)

    all_slots = []
    total_marks = 0
    record_count = 0

    for group in groups:
        key = (
            group["number_from"],
            group["number_to"],
        )

        passage_number = group["passage_number"]

        if passage_number not in contexts:
            raise ValueError(
                f"Invalid passage for group {key}"
            )

        result = prepare_group(
            group,
            ir_by_group[key],
            contexts[passage_number],
            corrections,
            findings,
        )

        passages[
            passage_number - 1
        ]["question_groups"].append(result)

        for question in result["questions"]:
            all_slots.extend(
                question["numbers"]
            )
            total_marks += question["max_score"]
            record_count += 1

    if (
        all_slots != list(range(1, 41))
        or total_marks != 40
        or record_count != ir["question_records"]
    ):
        raise ValueError(
            "Final dataset coverage/score mismatch"
        )

    if (
        qa.get("question_records") != record_count
        or qa.get("max_score") != total_marks
    ):
        raise ValueError(
            "Finalize and QA results disagree"
        )

    old_warnings = qa.get("warnings") or []

    resolved, unresolved = recheck_qa_warnings(
        passages,
        old_warnings,
    )

    review_items = unique(
        unresolved + findings
    )

    status = (
        "PREVIEW_READY_PENDING_APPROVAL"
        if not review_items
        else "CONTENT_REVIEW_REQUIRED"
    )

    report = {
        "status": status,
        "passages": len(passages),
        "groups": len(groups),
        "question_slots": len(all_slots),
        "question_records": record_count,
        "max_score": total_marks,
        "source_qa_warnings": old_warnings,
        "resolved_source_warnings": resolved,
        "warnings": review_items,
        "review_items": review_items,
        "corrections": corrections,
        "requires_visual_approval": True,
        "import_ready": False,
    }

    dataset = {
        "test": TEST_TITLE,
        "status": status,
        "import_ready": False,
        "contract_version": 1,
        "answer_source_page": ir["source_answer_page"],
        "passages": passages,
    }

    DEST.parent.mkdir(
        parents=True, exist_ok=True
    )

    DEST.write_text(
        json.dumps(
            dataset,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report_path = (
        DEST.parent / "finalize_report.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n--- STAGE 6 IR FINALIZE ---")
    print("Passages:", len(passages))
    print("Groups:", len(groups))
    print("Question slots:", len(all_slots))
    print("Gradable records:", record_count)
    print("Marks:", total_marks)
    print("Original QA warnings:", len(old_warnings))
    print("Remaining review items:", len(review_items))
    print("Corrections:", len(corrections))

    for warning in review_items:
        print(" REVIEW:", warning)

    print("Status:", status)
    print("Output:", DEST)
    print("Report:", report_path)
    print("IMPORT NOT APPROVED")


def main():
    finalize()


if __name__ == "__main__":
    main()
