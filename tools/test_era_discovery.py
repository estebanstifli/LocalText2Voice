"""Read-only isolated era experiments from an existing failed analysis request.

No application settings or project data are changed. Outputs go to a fresh folder.
The final case is an explicitly labelled synthetic positive control, not book text.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
import time

import requests


SHORT = (
    'Identify historical periods explicitly stated in the current narration. '
    'Return only new era events using the supplied schema and current unit IDs. '
    'If no historical period is stated, return {"era_events": []}. '
    'Do not infer a period from houses, animals or ordinary actions. '
    'Do not repeat known eras.'
)
EVIDENCE = (
    'Extract only explicit historical dates or named historical periods from the current narration. '
    'Return the supplied JSON schema. Each event needs a current unit ID and an exact quote '
    'from that unit naming the date or period. Known eras are context, not evidence. '
    'Return {"era_events": []} when no new date or period is stated. '
    'Do not infer dates from materials, buildings, customs or actions.'
)


def load_request(path):
    text = path.read_text(encoding='utf-8')
    for match in re.finditer(r'^REQUEST \d+: continuity structurer block 4/8 / era\s*$', text, re.M):
        body, _ = json.JSONDecoder().raw_decode(text[text.index('{', match.end()):])
        return body
    raise ValueError('Failed block 4/8 era request not found')


def bounded(body):
    body = deepcopy(body)
    context = json.loads(body['messages'][-1]['content'])
    context['known_eras'] = [
        {key: row.get(key) for key in ('id', 'description')}
        for row in context.get('known_eras', [])
    ]
    ids = [unit['unit_id'] for unit in context['narration_units']]
    body['format']['properties']['era_events']['items']['properties']['effective_unit_id'] = {
        'type': 'integer', 'enum': ids}
    body['messages'][-1]['content'] = json.dumps(context, ensure_ascii=False)
    return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-log', type=Path, required=True)
    parser.add_argument('--url', default='http://127.0.0.1:11434')
    args = parser.parse_args()
    original = load_request(args.input_log)
    cases = [('01_original', deepcopy(original)), ('02_short', deepcopy(original)),
             ('03_literal_evidence', deepcopy(original)), ('04_bounded', bounded(original))]
    cases[1][1]['messages'][0]['content'] = SHORT
    for _, body in cases[2:]:
        body['messages'][0]['content'] = EVIDENCE
    control = deepcopy(cases[-1][1])
    control['messages'][-1]['content'] = json.dumps({
        'narration_units': [
            {'unit_id': 33, 'narration': 'In 1850, Clara lived in London.'},
            {'unit_id': 34, 'narration': 'She wore a blue coat.'},
            {'unit_id': 35, 'narration': 'A century later, in 1950, her granddaughter returned.'}],
        'known_eras': []})
    control['format']['properties']['era_events']['items']['properties']['effective_unit_id']['enum'] = [33, 34, 35]
    cases.append(('05_synthetic_positive_control', control))
    root = Path('tmp/era-discovery') / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    root.mkdir(parents=True)
    print('Output:', root.resolve(), flush=True)
    summary = []
    for name, body in cases:
        folder = root / name
        folder.mkdir()
        body['stream'] = True
        (folder / 'request.json').write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding='utf-8')
        print('\nCASE:', name, flush=True)
        started = time.monotonic()
        content = ''
        done = {}
        try:
            with requests.post(args.url.rstrip('/') + '/api/chat', json=body, stream=True,
                               timeout=(15, 180)) as response:
                response.raise_for_status()
                with (folder / 'raw-response.jsonl').open('w', encoding='utf-8') as raw, (folder / 'response.txt').open('w', encoding='utf-8') as report:
                    for line in response.iter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        raw.write(json.dumps(chunk, ensure_ascii=False) + '\n')
                        raw.flush()
                        if chunk.get('error'):
                            raise RuntimeError(chunk['error'])
                        delta = chunk.get('message', {}).get('content', '')
                        content += delta
                        report.write(delta)
                        report.flush()
                        if chunk.get('done'):
                            done = chunk
            if not done:
                raise RuntimeError('Incomplete stream')
            parsed = json.loads(content)
            events = parsed.get('era_events', [])
            context = json.loads(body['messages'][-1]['content'])
            units = {u['unit_id']: u['narration'] for u in context['narration_units']}
            stats = {'case': name, 'seconds': round(time.monotonic()-started, 2),
                     'finish_reason': done.get('done_reason'), 'events': len(events),
                     'invalid_unit_ids': [e.get('effective_unit_id') for e in events if e.get('effective_unit_id') not in units],
                     'nonliteral_evidence': sum(not e.get('evidence') or e['evidence'] not in units.get(e.get('effective_unit_id'), '') for e in events)}
            summary.append(stats)
            print(content, flush=True)
            print(json.dumps(stats), flush=True)
        except Exception as exc:
            (folder / 'error.txt').write_text(str(exc), encoding='utf-8')
            summary.append({'case': name, 'error': str(exc)})
            print('ERROR:', exc, flush=True)
        (root / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
