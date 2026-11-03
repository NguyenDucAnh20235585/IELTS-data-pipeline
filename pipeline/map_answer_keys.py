
"""Core answer normalization and IR for IELTS Reading (no test-specific config)."""
import json
import re
from pathlib import Path

PAGE = Path('output/stage1/pages/page_119.json')
QUESTIONS = Path('output/stage3/question_candidates_reviewed.json')
OUT = Path('output/stage4')
ANSWER_PAGE = 119
EXPECTED_GROUP_COUNT = None  # Set by the existing runner; not required for IR.

DB_TYPES = {
    'MULTIPLE_CHOICE': 'mcq_single',
    'MULTIPLE_CHOICE_TWO': 'mcq_multi',
    'MULTIPLE_CHOICE_THREE': 'mcq_multi',
    'MULTIPLE_CHOICE_MULTI': 'mcq_multi',
    'TRUE_FALSE_NOT_GIVEN': 'true_false_not_given',
    'YES_NO_NOT_GIVEN': 'yes_no_not_given',
    'MATCHING_INFORMATION': 'matching_information',
    'MATCHING_HEADINGS': 'matching_headings',
    'MATCHING_FEATURES': 'matching_features',
    'MATCHING_SENTENCE_ENDINGS': 'matching_sentence_endings',
    'SENTENCE_COMPLETION': 'sentence_completion',
    'SUMMARY_COMPLETION': 'summary_completion',
    'SUMMARY_WORD_BANK': 'summary_completion',
    'NOTE_COMPLETION': 'note_completion',
    'TABLE_COMPLETION': 'table_completion',
    'FLOW_CHART_COMPLETION': 'flow_chart_completion',
    'FORM_COMPLETION': 'form_completion',
    'DIAGRAM_LABEL_COMPLETION': 'diagram_label_completion',
    'SHORT_ANSWER': 'short_answer',
    'MAP_PLAN_LABELLING': 'map_plan_labelling',
}

TEXT_ONLY = {
    'SENTENCE_COMPLETION',
    'NOTE_COMPLETION',
    'TABLE_COMPLETION',
    'SUMMARY_COMPLETION',
    'FLOW_CHART_COMPLETION',
    'FORM_COMPLETION',
    'SHORT_ANSWER',
    'DIAGRAM_LABEL_COMPLETION',
    'MAP_PLAN_LABELLING',
}

CHOICE_ONLY = {
    'MULTIPLE_CHOICE',
    'MULTIPLE_CHOICE_TWO',
    'MULTIPLE_CHOICE_THREE',
    'MULTIPLE_CHOICE_MULTI',
    'TRUE_FALSE_NOT_GIVEN',
    'YES_NO_NOT_GIVEN',
    'MATCHING_INFORMATION',
    'MATCHING_HEADINGS',
    'MATCHING_FEATURES',
    'MATCHING_SENTENCE_ENDINGS',
    'SUMMARY_WORD_BANK',
}

NUMBER_LINE = re.compile(
    r'^([1-9]|[1-3]\d|40)(?:\s+(.+))?$'
)
PAIR_LINE = re.compile(
    r'^(\d{1,2})\s*(?:&|AND)\s*(\d{1,2})$', re.I
)
COUNT_WORDS = {
    'ONE': 1,
    'TWO': 2,
    'THREE': 3,
    'FOUR': 4,
    'FIVE': 5,
}


def compact(value):
    return re.sub(r'\s+', ' ', str(value)).strip()


def norm_key(value):
    s = compact(value).upper()
    return re.sub(r'\bNOT\s*GIVEN\b', 'NOT GIVEN', s)


# Compatibility with run_pipeline.py
def normalize(value):
    return norm_key(value)


def parse_answer_key(page):
    lines = [
        compact(line)
        for line in page['text'].splitlines()
        if compact(line)
    ]

    try:
        start = next(
            i for i, line in enumerate(lines)
            if line.upper() == 'READING'
        ) + 1
    except StopIteration as exc:
        raise ValueError(
            'READING answer key heading missing'
        ) from exc

    answers, pairs = {}, {}
    i = start

    while i < len(lines):
        line = lines[i]
        pair = PAIR_LINE.fullmatch(line)

        if pair:
            first, last = map(int, pair.groups())

            if last != first + 1 or i + 3 >= len(lines):
                raise ValueError(
                    f'Invalid paired answer marker: {line}'
                )

            if norm_key(lines[i + 1]) != 'IN EITHER ORDER':
                raise ValueError(
                    f'Unrecognized pair instructions at {line}'
                )

            values = [
                norm_key(lines[i + 2]),
                norm_key(lines[i + 3]),
            ]

            if (
                any(
                    not re.fullmatch('[A-Z]', v)
                    for v in values
                )
                or len(set(values)) != 2
            ):
                raise ValueError(
                    f'Invalid unordered choices: {values}'
                )

            if any(
                n in answers for n in (first, last)
            ):
                raise ValueError(
                    f'Duplicate answer pair {first}-{last}'
                )

            answers[first], answers[last] = values
            pairs[(first, last)] = values
            i += 4
            continue

        entry = NUMBER_LINE.fullmatch(line)

        if entry:
            number = int(entry.group(1))
            value = compact(entry.group(2) or '')

            if not value:
                if i + 1 == len(lines):
                    raise ValueError(
                        f'Missing answer at Q{number}'
                    )

                value = lines[i + 1]

                if (
                    NUMBER_LINE.fullmatch(value)
                    or PAIR_LINE.fullmatch(value)
                ):
                    raise ValueError(
                        f'Missing answer at Q{number}'
                    )

                i += 1

            if number in answers:
                raise ValueError(
                    f'Duplicate answer at Q{number}'
                )

            answers[number] = norm_key(value)

        i += 1

    expected = set(range(1, 41))

    if set(answers) != expected:
        raise ValueError(
            'Answer key coverage: '
            f'missing={sorted(expected - set(answers))}, '
            f'extra={sorted(set(answers) - expected)}'
        )

    return answers, pairs


def word_rule(group):
    instruction = compact(
        group.get('raw_group_text', '')
    ).upper()

    matches = re.findall(
        r'\b(ONE|TWO|THREE|FOUR|FIVE|[1-9])\s+WORDS?\b',
        instruction,
    )

    if not matches:
        raise ValueError(
            f"Q{group['number_from']}: "
            "missing word limit in instructions"
        )

    count = max(
        COUNT_WORDS.get(
            m, int(m) if m.isdigit() else 0
        )
        for m in matches
    )

    allow_number = bool(
        re.search(
            r'\b(?:AND/OR|OR)\s+(?:A\s+)?NUMBER\b',
            instruction,
        )
    )

    if count > 10:
        raise ValueError('Unsupported word limit')

    return count, allow_number


def text_variants(raw, max_words, allow_number):
    # Explicit alternatives only, no fuzzy matching.
    options = re.split(
        r'\s+(?:I|/|OR)\s+',
        compact(raw),
    )

    accepted = []

    for option in options:
        part = option.strip().lower()

        optional = re.fullmatch(
            r'(.+?)\s*\(([^()]+)\)',
            part,
        )

        values = (
            [
                optional.group(1).strip(),
                (
                    optional.group(1)
                    + ' '
                    + optional.group(2)
                ).strip(),
            ]
            if optional else [part]
        )

        for value in values:
            tokens = value.split()

            words = sum(
                not bool(
                    re.fullmatch(
                        r'\d+(?:[.,:/]\d+)*',
                        t,
                    )
                )
                for t in tokens
            )

            numbers = len(tokens) - words

            if allow_number:
                valid = (
                    words <= max_words
                    and numbers <= 1
                )
            else:
                valid = (
                    words + numbers <= max_words
                )

            if (
                not value
                or not valid
                or len(value) > 200
            ):
                raise ValueError(
                    'Text answer violates word rule: '
                    f'{value!r}'
                )

            if value not in accepted:
                accepted.append(value)

    if len(accepted) > 20:
        raise ValueError(
            'Too many accepted variants'
        )

    return accepted


def canonical_choice(raw, options):
    keys = [o['key'] for o in options]

    if not keys or len(keys) != len(set(keys)):
        raise ValueError(
            'Missing/duplicate option keys'
        )

    lookup = {
        norm_key(key): key
        for key in keys
    }

    answer = lookup.get(norm_key(raw))

    if answer is None:
        raise ValueError(
            f'{raw!r} is not among option keys {keys}'
        )

    return answer


def build_answer_ir(data, answers, pairs):
    """Build IR without modifying reviewed source."""
    groups = data['groups']
    seen = []
    items = []
    used_pairs = set()

    for group in groups:
        kind = group['question_type_hint']

        if kind not in DB_TYPES:
            raise ValueError(
                f'Unsupported question type: {kind}'
            )

        start = group['number_from']
        end = group['number_to']
        numbers = list(range(start, end + 1))
        records = group['questions']

        if [q['number'] for q in records] != numbers:
            raise ValueError(
                f'Q{start}-{end}: invalid question ordering'
            )

        if group.get('warnings'):
            raise ValueError(
                f'Q{start}-{end}: unresolved warnings'
            )

        for q in records:
            if q.get('status') not in (
                'EXTRACTED_CANDIDATE',
                'REVIEWED_CANDIDATE',
            ):
                raise ValueError(
                    f"Q{q['number']}: needs review"
                )

        seen.extend(numbers)
        shared = group.get('shared_options') or []

        select_match = re.search(
            r'\bCHOOSE\s+'
            r'(TWO|THREE|FOUR|FIVE|[2-5])'
            r'\s+LETTERS?\b',
            compact(
                group.get('raw_group_text', '')
            ).upper(),
        )

        multi_kinds = {
            'MULTIPLE_CHOICE_TWO',
            'MULTIPLE_CHOICE_THREE',
            'MULTIPLE_CHOICE_MULTI',
        }

        is_multi = (
            kind in multi_kinds
            or (
                kind == 'MULTIPLE_CHOICE'
                and bool(select_match)
            )
        )

        if is_multi:
            if not select_match:
                raise ValueError(
                    f'Q{start}-{end}: '
                    'cannot detect multi-choice count'
                )

            token = select_match.group(1)
            select_count = COUNT_WORDS.get(
                token,
                int(token) if token.isdigit() else 0,
            )

            if (
                select_count != len(numbers)
                or not 2 <= select_count <= 5
            ):
                raise ValueError(
                    f'Q{start}-{end}: '
                    'multi-choice count mismatch'
                )

            chosen = [
                canonical_choice(
                    answers[n], shared
                )
                for n in numbers
            ]

            if len(set(chosen)) != len(chosen):
                raise ValueError(
                    f'Q{start}-{end}: '
                    'duplicate correct choices'
                )

            key = (start, end)

            if key in pairs:
                if set(pairs[key]) != set(chosen):
                    raise ValueError(
                        f'Q{start}-{end}: '
                        'paired key mismatch'
                    )

                used_pairs.add(key)

            review_rule = group.get('grading_rule')

            if review_rule and (
                review_rule.get('mode')
                != 'unordered_unique_choices'
                or set(
                    review_rule.get(
                        'correct_choices', []
                    )
                ) != set(chosen)
            ):
                raise ValueError(
                    f'Q{start}-{end}: '
                    'review rule disagrees with PDF'
                )

            if (
                len({
                    q.get('prompt')
                    for q in records
                }) != 1
                or not records[0].get('prompt')
            ):
                raise ValueError(
                    f'Q{start}-{end}: no shared prompt'
                )

            items.append({
                'numbers': numbers,
                'question_type': 'mcq_multi',
                'answer_format': 'multi_choice',
                'max_score': select_count,
                'config': {
                    'select_count': select_count
                },
                'correct_answer': {
                    'choices': chosen
                },
                'grading_config': {},
                'source_group': [start, end],
            })

            continue

        for q in records:
            n = q['number']
            options = (
                q.get('options') or shared
            )

            if kind in TEXT_ONLY and not options:
                max_words, allow_number = word_rule(
                    group
                )

                accepted = text_variants(
                    answers[n],
                    max_words,
                    allow_number,
                )

                answer_format = 'text'
                correct_answer = {
                    'accepted': accepted
                }
                config = {
                    'max_words': max_words,
                    'allow_number': allow_number,
                }
                grading_config = {
                    'case_sensitive': False,
                    'strip_punctuation': True,
                }

            elif kind in CHOICE_ONLY or options:
                choice = canonical_choice(
                    answers[n], options
                )

                answer_format = 'choice'
                correct_answer = {
                    'choice': choice
                }
                config = {}
                grading_config = {}

            else:
                raise ValueError(
                    f'Q{n}: ambiguous answer format'
                )

            if q.get('render_mode') == 'INLINE_GAP':
                token = q.get('gap_token')

                if (
                    not token
                    or (
                        group.get('render_template') or ''
                    ).count(token) != 1
                ):
                    raise ValueError(
                        f'Q{n}: invalid gap token'
                    )

            elif not q.get('prompt'):
                raise ValueError(
                    f'Q{n}: missing prompt'
                )

            items.append({
                'numbers': [n],
                'question_type': DB_TYPES[kind],
                'answer_format': answer_format,
                'max_score': 1,
                'config': config,
                'correct_answer': correct_answer,
                'grading_config': grading_config,
                'source_group': [start, end],
            })

    if seen != list(range(1, 41)):
        raise ValueError(
            f'Invalid question coverage: {seen}'
        )

    if set(pairs) != used_pairs:
        raise ValueError(
            'Unmatched paired answers in PDF: '
            f'{set(pairs) - used_pairs}'
        )

    if sum(
        item['max_score'] for item in items
    ) != 40:
        raise ValueError(
            'Total score is not 40'
        )

    return items


def main():
    page = json.loads(
        PAGE.read_text(encoding='utf-8')
    )

    data = json.loads(
        QUESTIONS.read_text(encoding='utf-8')
    )

    if page.get('pdf_page') != ANSWER_PAGE:
        raise ValueError(
            'Wrong answer page '
            f'{page.get("pdf_page")}, '
            f'expected {ANSWER_PAGE}'
        )

    answers, pairs = parse_answer_key(page)
    items = build_answer_ir(
        data, answers, pairs
    )

    result = {
        'status': 'ANSWER_IR_VALIDATED_IMPORT_BLOCKED',
        'contract_version': 1,
        'source_answer_page': ANSWER_PAGE,
        'answers': {
            str(n): answers[n]
            for n in sorted(answers)
        },
        'answer_items': items,
        'question_slots': 40,
        'question_records': len(items),
        'max_score': sum(
            x['max_score'] for x in items
        ),
    }

    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = OUT / 'answer_ir.json'

    destination.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    print(
        'ANSWER IR PASS: '
        f'{len(answers)} slots / '
        f'{len(items)} gradable records / '
        '40 marks'
    )

    print('Output:', destination)
    print(
        'IMPORT BLOCKED: QA/finalize/importer '
        'must consume the new IR first.'
    )


if __name__ == '__main__':
    main()
