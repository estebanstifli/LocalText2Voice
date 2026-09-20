"""Read an existing request and test a shorter scene prompt without changing the project."""
import json
from pathlib import Path
import re
import time
from datetime import datetime
from urllib.request import Request, urlopen


def main():
    root = Path(__file__).resolve().parents[1]
    log = root / 'projects/Project4 (2)/storyboard/debug/analysis-requests-20260911-124437-400142.txt'
    text = log.read_text(encoding='utf-8-sig')
    marker = re.search(r'^REQUEST 3:.*$', text, re.M)
    payload = json.JSONDecoder().raw_decode(text[text.index('{', marker.end()):])[0]
    source = payload['messages'][0]['content'].split('\n\n', 1)[1]
    prompt = ('What scenes would you illustrate in this excerpt? For each, briefly describe the image '
              'and quote its opening sentence. Number them 2-1, 2-2, etc.')
    payload['messages'] = [{'role': 'user', 'content': prompt + '\n\n' + source}]
    payload['stream'] = True
    payload['think'] = True
    output = root / 'tmp/scene-prompt-tests' / datetime.now().strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True)
    (output / 'request.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    (output / 'prompt-and-text.txt').write_text(payload['messages'][0]['content'], encoding='utf-8')
    print('OUTPUT:', output, flush=True)
    start = time.monotonic()
    last = start
    answer = ''
    final = {}
    req = Request('http://127.0.0.1:11434/api/chat',
                  data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=600) as response:
        for line in response:
            event = json.loads(line)
            if event.get('error'):
                raise RuntimeError(event['error'])
            answer += event.get('message', {}).get('content', '')
            if time.monotonic() - last > 15:
                print(f'Elapsed {time.monotonic() - start:.1f}s; answer {len(answer)} characters', flush=True)
                last = time.monotonic()
            if event.get('done'):
                final = {k: v for k, v in event.items() if k != 'message'}
    (output / 'response.txt').write_text(answer, encoding='utf-8')
    final['elapsed_seconds'] = time.monotonic() - start
    final['source_log'] = str(log)
    (output / 'metrics.json').write_text(json.dumps(final, indent=2), encoding='utf-8')
    print(json.dumps(final), flush=True)
    print(answer, flush=True)


if __name__ == '__main__':
    main()
