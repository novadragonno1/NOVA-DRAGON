"""JSON in, JSON out; parameters never become shell commands."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integrations.bridge import run
from nova_runtime import TaskError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request')
    parser.add_argument('--params-file')
    options = parser.parse_args()
    try:
        if options.params_file:
            with open(options.params_file, 'rb') as handle:
                raw = handle.read(20_001)
        else:
            raw = (options.request or '{}').encode()
        if len(raw) > 20_000:
            raise TaskError('Request too large.')
        request = json.loads(raw)
        # OpenCrabs tool passes the entire request as one JSON string parameter.
        if isinstance(request, dict) and set(request) == {'request'}:
            request = json.loads(request['request'])
        result = run(request)
        output = json.dumps(result, ensure_ascii=False)
        for key, value in os.environ.items():
            if len(value) >= 8 and any(word in key.upper() for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')):
                output = output.replace(value, '[REDACTED]')
        if len(output.encode()) > 400_000:
            raise TaskError('Result too large; narrow the request.')
        print(output)
    except TaskError as error:
        print(json.dumps({'isError': True, 'error': str(error)}, ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({'isError': True, 'error': 'Tool connection/call failed. Check installation, credentials and service availability.'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
