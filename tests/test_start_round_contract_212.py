import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / 'docs/PROTOCOL.md').read_text()
SECTION = DOC.split('### 5.6a `start_round`', 1)[1].split('### 5.7', 1)[0]


def test_examples_are_fixed_ascii_envelopes():
    examples = [json.loads(x) for x in re.findall(r'```json\n(.*?)\n```', SECTION, re.S)]
    assert examples[0] == {'protocol_version': '1.0', 'req_id': 'round-dfu01-001',
                           'device': 'DFU-01', 'cmd': 'start_round', 'timeout_sec': 30}
    replies = examples[1:]
    assert len(replies) == 6
    assert {(r['status'], r['data']['kind'], r['error']) for r in replies} == {
        ('ok', 'started', ''), ('error', 'paused', 'log_solution_review'),
        ('error', 'paused', 'hid_failed'), ('error', 'fault', 'frame_frozen'),
        ('timeout', 'fault', 'timeout'), ('error', 'fault', 'unsupported_command')}
    for r in replies:
        assert set(r) == {'protocol_version', 'req_id', 'device', 'cmd', 'status', 'error', 'message', 'elapsed_sec', 'data'}
        assert r['protocol_version'] == '1.0' and set(r['data']) == {'kind'}
        assert None not in r.values()
        json.dumps(r, ensure_ascii=False).encode('ascii')
        if r['status'] == 'ok':
            assert r['error'] == r['message'] == ''


def test_contract_tables_and_boundaries():
    rows = [line.split('|')[1:-1] for line in SECTION.splitlines() if line.startswith('|')]
    assert any('1–300' in row[0] and '接受' in row[1] for row in rows)
    assert any('布林' in row[0] and 'invalid_argument' in row[1] for row in rows)
    retry = {row[0].strip() for row in rows if len(row) == 7}
    assert {'not_ready', 'no_frame', 'busy', '外部鎖衝突', 'poll timeout', 'review', 'HID 不確定', 'fault'} <= retry
    for term in ('D = T + 73', '110', '145', '480', '510', '>=106', '不重設 T', 'Core 重啟', 'on_accept → begin_start', '沒有時間上限'):
        assert term in SECTION
    adr = (ROOT / 'docs/adr/0002-per-device-lock-and-fail-fast.md').read_text()
    assert all(term in adr for term in ('start_round', 'device_busy', '不同設備獨立'))
    assert '尚待 #213 實作' in DOC and 'unknown_command' in DOC
