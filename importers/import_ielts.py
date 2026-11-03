
"""Import approved IELTS Stage-6 datasets as isolated, hidden QA drafts.

One importer for Reading/Listening question structures, independent of book/test.
Dry-run by default; --apply requires hash-bound content approval.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

TYPE_MAP = {
    'NOTE_COMPLETION': 'note_completion',
    'TRUE_FALSE_NOT_GIVEN': 'true_false_not_given',
    'MATCHING_INFORMATION': 'matching_information',
    'SUMMARY_COMPLETION': 'summary_completion',
    'MATCHING_FEATURES': 'matching_features',
    'MULTIPLE_CHOICE': 'mcq_single',
    'SUMMARY_WORD_BANK': 'summary_completion',
}
ALLOWED = {
    'mcq_single': {'choice'}, 'mcq_multi': {'multi_choice'},
    'true_false_not_given': {'choice'}, 'yes_no_not_given': {'choice'},
    'matching_headings': {'choice'}, 'matching_information': {'choice'},
    'matching_features': {'choice'}, 'matching_sentence_endings': {'choice'},
    'sentence_completion': {'text', 'choice'},
    'summary_completion': {'text', 'choice'}, 'note_completion': {'text'},
    'table_completion': {'text'}, 'form_completion': {'text'},
    'flow_chart_completion': {'text', 'choice'},
    'diagram_label_completion': {'text'}, 'map_plan_labelling': {'choice', 'text'},
    'short_answer': {'text'},
}
FIXED = {
    'true_false_not_given': {'TRUE', 'FALSE', 'NOT_GIVEN'},
    'yes_no_not_given': {'YES', 'NO', 'NOT_GIVEN'},
}
GAP_RE = re.compile(r'\{\{gap:(\d+)\}\}|\[(\d+):\s*_+\]')
RULE_KEYS = {'max_words', 'allow_number', 'select_count', 'allow_option_reuse'}


def ensure(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized_key(value):
    return re.sub(r'\s+', '_', str(value).strip().upper())


def normalize_options(values, label):
    if values is None:
        return None
    ensure(isinstance(values, list) and 1 <= len(values) <= 40,
           f'{label}: invalid option list')
    result = []
    for row in values:
        ensure(isinstance(row, dict), f'{label}: malformed option')
        k = normalized_key(row.get('key', ''))
        title = row.get('text')
        ensure(1 <= len(k) <= 10 and isinstance(title, str)
               and 1 <= len(title.strip()) <= 500, f'{label}: invalid option')
        result.append({'key': k, 'text': title.strip()})
    ensure(len({v['key'] for v in result}) == len(result),
           f'{label}: duplicate option key')
    return result


def normalize_rules(raw, label):
    ensure(isinstance(raw, dict), f'{label}: rules/config must be object')
    ensure(set(raw) <= RULE_KEYS, f'{label}: unknown rule keys {set(raw) - RULE_KEYS}')
    for k in ('max_words', 'select_count'):
        if raw.get(k) is not None:
            lo = 0 if k == 'max_words' else 2
            hi = 10 if k == 'max_words' else 5
            ensure(type(raw[k]) is int and lo <= raw[k] <= hi,
                   f'{label}: bad {k}')
    for k in ('allow_number', 'allow_option_reuse'):
        if k in raw:
            ensure(type(raw[k]) is bool, f'{label}: bad {k}')
    return raw


def group_text(group):
    instructions = group.get('instructions')
    content = group.get('content')
    template = group.get('render_template')
    if content is None and isinstance(template, str) and template.strip():
        # Legacy Stage-6 layout: header lines followed by gap body.
        lines = template.splitlines()
        count = 2 if group.get('question_type_hint') == 'SUMMARY_WORD_BANK' else 3
        ensure(len(lines) > count, 'Legacy inline template is too short')
        instructions = instructions or ' '.join(line.strip() for line in lines[:count])
        content = '\n'.join(lines[count:]).strip()
    ensure(instructions is None or isinstance(instructions, str), 'Invalid instructions')
    ensure(content is None or isinstance(content, str), 'Invalid group content')
    return instructions, content


def normalize_dataset(raw, *, expected_marks=40):
    title = raw.get('test') or raw.get('title')
    ensure(isinstance(title, str) and len(title.strip()) >= 5,
           'Dataset missing test/title')
    kind = str(raw.get('type') or (
        'listening' if 'listening' in title.lower() else 'reading'
    )).lower()
    ensure(kind in ('reading', 'listening'), 'Only IELTS Reading/Listening supported')
    passages = raw.get('sections') or raw.get('passages')
    ensure(isinstance(passages, list) and passages, 'No sections/passages')
    output, slots, total_records, group_count = [], [], 0, 0
    for section_index, section in enumerate(passages, 1):
        ensure(section.get('number', section_index) == section_index,
               f'Section {section_index}: wrong ordering')
        body = section.get('content')
        if kind == 'reading':
            ensure(isinstance(body, str) and bool(body.strip()),
                   f'Section {section_index}: passage text missing')
        audio_key = section.get('audio_object_key')
        if kind == 'listening':
            ensure(isinstance(audio_key, str) and bool(audio_key.strip()),
                   f'Section {section_index}: missing audio_object_key')
        raw_groups = section.get('question_groups') or section.get('groups')
        ensure(isinstance(raw_groups, list) and raw_groups,
               f'Section {section_index}: no question groups')
        groups = []
        for group_index, group in enumerate(raw_groups, 1):
            first, last = group.get('number_from'), group.get('number_to')
            original_questions = group.get('questions')
            ensure(isinstance(original_questions, list) and original_questions,
                   f'Section {section_index} group {group_index}: no questions')
            instructions, content = group_text(group)
            shared = normalize_options(group.get('options', group.get('shared_options')),
                                       f'Group {group_index}')
            rules = normalize_rules(group.get('rules') or {}, f'Group {group_index}')
            if instructions and 'may use any letter more than once' in instructions.lower():
                rules['allow_option_reuse'] = True
            prepared = []
            group_slots = []
            for question_index, q in enumerate(original_questions, 1):
                n = q.get('display_number', q.get('number'))
                ensure(type(n) is int and n > 0, f'Group {group_index}: invalid number')
                question_type = str(q.get('question_type') or TYPE_MAP.get(
                    q.get('question_type_hint') or group.get('question_type_hint'),
                    q.get('question_type_hint') or group.get('question_type_hint') or ''
                )).lower()
                fmt = str(q.get('answer_format') or '').lower()
                ensure(question_type in ALLOWED and fmt in ALLOWED[question_type],
                       f'Q{n}: unsupported type/format {question_type}/{fmt}')
                config = normalize_rules(q.get('config') or {}, f'Q{n}')
                marks = q.get('max_score')
                if marks is None:
                    marks = config.get('select_count') if fmt == 'multi_choice' else 1
                ensure(type(marks) is int and 1 <= marks <= 5,
                       f'Q{n}: invalid max_score')
                ensure((fmt == 'multi_choice') == (marks > 1),
                       f'Q{n}: marks/format conflict')
                if fmt == 'multi_choice':
                    ensure(config.get('select_count', rules.get('select_count')) == marks,
                           f'Q{n}: select_count must equal marks')
                numbers = q.get('numbers') or list(range(n, n + marks))
                ensure(numbers == list(range(n, n + marks)),
                       f'Q{n}: invalid numbers/marks')
                
                own = normalize_options(q.get('options'), f'Q{n}')

                if question_type in FIXED:
                    # TFNG / YNNG use built-in options.
                    own = None
                    shared = None

                elif fmt == 'text':
                    # Text questions do not use choice options.
                    own = None

                elif own == shared:
                    # Use group options when both lists are identical.
                    own = None

                # Question options override group options.
                effective = own or shared

                answers = q.get('correct_answer')
                ensure(isinstance(answers, dict), f'Q{n}: answer key missing')
                grading = q.get('grading_config') or {}
                ensure(isinstance(grading, dict), f'Q{n}: bad grading_config')
                if fmt == 'text':
                    limit = config.get('max_words', rules.get('max_words'))
                    ensure(type(limit) is int and 1 <= limit <= 10,
                           f'Q{n}: missing word limit')
                    accepted = answers.get('accepted')
                    ensure(isinstance(accepted, list) and 1 <= len(accepted) <= 20
                           and all(isinstance(a, str) and 1 <= len(a.strip()) <= 200
                                   for a in accepted), f'Q{n}: invalid accepted answers')
                    ensure(set(grading) <= {'case_sensitive', 'strip_punctuation'}
                           and all(type(v) is bool for v in grading.values()),
                           f'Q{n}: invalid grading_config')
                    answer = {'accepted': [a.strip() for a in accepted]}
                else:
                    ensure(not grading, f'Q{n}: grading_config only for TEXT')
                    keys = FIXED.get(question_type) or {v['key'] for v in effective or []}
                    if fmt == 'choice':
                        choice = normalized_key(answers.get('choice', ''))
                        ensure(choice in keys, f'Q{n}: invalid choice {choice}')
                        answer = {'choice': choice}
                    else:
                        selections = answers.get('choices')
                        ensure(isinstance(selections, list) and len(selections) == marks,
                               f'Q{n}: invalid multi-choice count')
                        selections = [normalized_key(v) for v in selections]
                        ensure(len(set(selections)) == marks and set(selections) <= keys,
                               f'Q{n}: invalid multi-choice selections')
                        answer = {'choices': selections}
                prompt = q.get('prompt')
                if prompt is None or not str(prompt).strip():
                    ensure(content is not None and sum(
                        int(a or b) == n for a, b in GAP_RE.findall(content)
                    ) == 1, f'Q{n}: missing prompt and gap')
                    prompt = f'Complete blank {n} in the text above.'
                ensure(isinstance(prompt, str), f'Q{n}: invalid prompt')
                if str(q.get('render_mode', '')).lower() == 'inline_gap':
                    ensure(content is not None and sum(
                        int(a or b) == n for a, b in GAP_RE.findall(content)
                    ) == 1, f'Q{n}: inline gap missing/duplicate')
                group_slots.extend(numbers)
                slots.extend(numbers)
                prepared.append({
                    'number': n, 'position': question_index, 'max_score': marks,
                    'question_type': question_type, 'answer_format': fmt,
                    'prompt': prompt.strip(), 'config': config, 'options': own,
                    'correct_answer': answer, 'grading_config': grading,
                })
                total_records += 1
            if type(first) is int and type(last) is int:
                ensure(group_slots == list(range(first, last + 1)),
                       f'Q{first}-{last}: group numbering mismatch')
            if content:
                tokens = [int(a or b) for a, b in GAP_RE.findall(content)]
                ensure(len(tokens) == len(set(tokens)) and set(tokens) <= set(group_slots),
                       f'Group {group_index}: duplicated/out-of-range gaps')
            groups.append({
                'title': group.get('title') or f'Questions {group_slots[0]}–{group_slots[-1]}',
                'position': group_index, 'instructions': instructions, 'content': content,
                'options': shared, 'rules': rules, 'questions': prepared,
            })
            group_count += 1
        output.append({
            'title': section.get('title') or f'Section {section_index}',
            'position': section_index, 'content': body,
            'instructions': section.get('instructions'),
            'audio_object_key': audio_key, 'groups': groups,
        })
    ensure(slots == list(range(1, expected_marks + 1)),
           f'Question slots must be 1–{expected_marks}, got {slots}')
    ensure(sum(q['max_score'] for s in output for g in s['groups'] for q in g['questions'])
           == expected_marks, 'Incorrect total marks')
    return {'title': title.strip(), 'type': kind, 'sections': output,
            'sections_count': len(output), 'groups_count': group_count,
            'records_count': total_records, 'marks': expected_marks}


def source_file(stage6):
    candidates = []
    for file in stage6.glob('*.json'):
        if file.name in ('content_approval.json', 'finalize_report.json'):
            continue
        try:
            obj = read_json(file)
        except (ValueError, UnicodeError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get('passages') or obj.get('sections'), list):
            candidates.append(file)
    ensure(len(candidates) == 1,
           f'Expected one Stage-6 dataset; candidates: {[p.name for p in candidates]}')
    return candidates[0]


def check_approval(source, stage6):
    report_path = stage6 / 'finalize_report.json'
    approval_path = stage6 / 'content_approval.json'
    ensure(report_path.is_file() and approval_path.is_file(),
           'Missing finalization report or content approval')
    report, approval = read_json(report_path), read_json(approval_path)
    ensure(approval.get('status') == 'APPROVED_FOR_DRAFT_IMPORT',
           'Not approved for draft import')
    ensure(str(approval.get('dataset_sha256', '')).lower() == digest(source),
           'Dataset hash differs from approval')
    ensure(str(approval.get('report_sha256', '')).lower() == digest(report_path),
           'Finalization report hash differs from approval')
    review_items = report.get('review_items', [])
    ensure(isinstance(review_items, list)
           and set(review_items) == set(approval.get('reviewed_items', [])),
           'Manual review items not fully approved')
    ensure(report.get('errors', []) == [], 'Finalization report contains errors')
    return len(review_items)


def apply_draft(dataset, url, source, initial_sha):
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    title = dataset['title'] + ' FULL [QA]'
    with psycopg.connect(url, sslmode='require', connect_timeout=20,
                         prepare_threshold=None, row_factory=dict_row) as conn:
        print('DB host:', conn.info.host)
        print('DB user:', conn.info.user)
        ensure(input('Type CREATE HIDDEN QA DRAFT: ') == 'CREATE HIDDEN QA DRAFT',
               'Cancelled')
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (title,))
            cur.execute('SELECT id FROM public.tests WHERE title = %s', (title,))
            ensure(cur.fetchone() is None, f'Test already exists: {title}')
            cur.execute('''
                INSERT INTO public.tests(title, description, type, visibility, access_type)
                VALUES (%s, %s, %s, 'hidden', 'public') RETURNING id
            ''', (title, 'Imported from approved IELTS Stage-6 dataset', dataset['type']))
            test_id = cur.fetchone()['id']
            cur.execute('''
                INSERT INTO public.test_versions(test_id, version_number, status)
                VALUES (%s, 1, 'draft') RETURNING id
            ''', (test_id,))
            version_id = cur.fetchone()['id']
            cur.execute('''
                INSERT INTO public.test_version_modes
                  (test_version_id, mode, enabled, time_limit_seconds,
                   answer_visibility, allow_pause, allow_replay, allow_seek)
                VALUES
                  (%s, 'practice', true, NULL, 'after_submit', true, false, false),
                  (%s, 'mock', false, 3600, 'after_submit', false, false, false)
            ''', (version_id, version_id))
            for section in dataset['sections']:
                cur.execute('''
                    INSERT INTO public.sections
                      (test_version_id, section_type, title, instructions,
                       content, position, audio_object_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
                ''', (version_id, dataset['type'], section['title'], section['instructions'],
                      section['content'], section['position'], section['audio_object_key']))
                section_id = cur.fetchone()['id']
                for group in section['groups']:
                    cur.execute('''
                        INSERT INTO public.question_groups
                          (section_id, title, instructions, content, options, rules, position)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
                    ''', (section_id, group['title'], group['instructions'], group['content'],
                          Jsonb(group['options']) if group['options'] is not None else None,
                          Jsonb(group['rules']), group['position']))
                    group_id = cur.fetchone()['id']
                    for q in group['questions']:
                        cur.execute('''
                            INSERT INTO public.questions
                              (question_group_id, question_type, answer_format, prompt,
                               options, config, position, max_score, display_number)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
                        ''', (group_id, q['question_type'], q['answer_format'], q['prompt'],
                              Jsonb(q['options']) if q['options'] is not None else None,
                              Jsonb(q['config']), q['position'], q['max_score'], q['number']))
                        question_id = cur.fetchone()['id']
                        cur.execute('''
                            INSERT INTO public.answer_keys
                              (question_id, correct_answer, grading_config)
                            VALUES (%s,%s,%s)
                        ''', (question_id, Jsonb(q['correct_answer']), Jsonb(q['grading_config'])))
            cur.execute('''
                SELECT COUNT(DISTINCT s.id) sections, COUNT(DISTINCT g.id) groups,
                       COUNT(DISTINCT q.id) records, COUNT(ak.question_id) keys,
                       COALESCE(SUM(q.max_score),0) marks
                FROM public.sections s
                JOIN public.question_groups g ON g.section_id=s.id
                JOIN public.questions q ON q.question_group_id=g.id
                LEFT JOIN public.answer_keys ak ON ak.question_id=q.id
                WHERE s.test_version_id=%s
            ''', (version_id,))
            got = cur.fetchone()
            expected = (dataset['sections_count'], dataset['groups_count'],
                        dataset['records_count'], dataset['records_count'], dataset['marks'])
            ensure(tuple(got[k] for k in ('sections','groups','records','keys','marks')) == expected,
                   f'DB counts mismatch: {got} != {expected}')
            cur.execute('''
                SELECT q.display_number, q.max_score
                FROM public.questions q JOIN public.question_groups g ON g.id=q.question_group_id
                JOIN public.sections s ON s.id=g.section_id
                WHERE s.test_version_id=%s
                ORDER BY s.position,g.position,q.position
            ''', (version_id,))
            slots = [n for r in cur.fetchall() for n in range(r['display_number'],
                                                           r['display_number']+int(r['max_score']))]
            ensure(slots == list(range(1, dataset['marks'] + 1)), 'DB slot numbering mismatch')
            cur.execute('''SELECT mode,enabled FROM public.test_version_modes
                           WHERE test_version_id=%s ORDER BY mode''', (version_id,))
            ensure([(r['mode'],r['enabled']) for r in cur.fetchall()]
                   == [('mock', False),('practice', True)], 'DB mode configuration mismatch')
            ensure(digest(source) == initial_sha, 'Source file changed during import')
            print('DB VERIFICATION PASS; test_id:', test_id, '; version_id:', version_id)
    print('TRANSACTION COMMITTED — Draft / Hidden')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--expected-marks', type=int, default=40)
    args = p.parse_args()
    stage6 = args.workspace / 'output' / 'stage6'
    ensure(stage6.is_dir(), f'Stage-6 directory not found: {stage6}')
    source = source_file(stage6)
    initial_sha = digest(source)
    normalized = normalize_dataset(read_json(source), expected_marks=args.expected_marks)
    print('DATASET:', source)
    print('SHA256:', initial_sha)
    print('TEST:', normalized['title'])
    print('TYPE:', normalized['type'])
    print('SECTIONS:', normalized['sections_count'], 'GROUPS:', normalized['groups_count'],
          'RECORDS:', normalized['records_count'], 'MARKS:', normalized['marks'])
    if not args.apply:
        try:
            n = check_approval(source, stage6)
            print(f'APPROVAL PASS ({n} reviewed items)')
        except (ValueError, FileNotFoundError) as exc:
            print('APPROVAL NOT READY:', exc)
        print('STRUCTURAL DRY-RUN PASS — NO DB WRITES')
        return
    count = check_approval(source, stage6)
    print(f'APPROVAL PASS ({count} reviewed items)')
    url = os.getenv('DATABASE_URL')
    ensure(bool(url), 'DATABASE_URL not configured')
    apply_draft(normalized, url, source, initial_sha)


if __name__ == '__main__':
    main()
