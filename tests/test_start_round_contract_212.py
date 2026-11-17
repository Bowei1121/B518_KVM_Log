import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / 'docs/PROTOCOL.md').read_text(encoding='utf-8')
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
        ('error', 'fault', 'unsupported_command'), ('timeout', 'fault', 'timeout')}
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
    for term in ('D = T + 73', '110', '145', '480', '510', '>=106', '不重設 T', 'Core 重啟', 'on_accept → begin_start', '使用者 2026-11-17 定案'):
        assert term in SECTION
    adr = (ROOT / 'docs/adr/0002-per-device-lock-and-fail-fast.md').read_text(encoding='utf-8')
    assert all(term in adr for term in ('start_round', 'device_busy', '不同設備獨立'))
    assert '尚待 #213 實作' in DOC and 'unknown_command' in DOC


def test_retry_and_confirmation_contract():
    rows = {r[0].strip(): r for r in (line.split('|')[1:-1] for line in SECTION.splitlines() if line.startswith('|')) if len(r) == 7}
    for name in ('not_ready', 'no_frame', 'busy', 'poll timeout'):
        assert '最多三次' in rows[name][4] and '0.5 秒' in rows[name][4]
        assert 'status=error' in rows[name][6] and 'kind=fault' in rows[name][6]
    assert 'check 長輪詢' not in rows['poll timeout'][2]
    assert '可信測試活動或有效結果' in SECTION and '確認窗口內未觀察到開始' in SECTION
    before = SECTION.split('開測 HID 送出前（使用者 2026-11-17 定案）：', 1)[1].split('同一設備以', 1)[0]
    assert '只有 D 截止且沒有原始錯誤碼時，回 `status=timeout`、`error=timeout`、`kind=fault`' in before
    assert 'D 截止且已有原碼，回 `status=error`、`kind=fault`，保留原碼' in before
    after = SECTION.split('同一設備以', 1)[1]
    for term in ('D 截止或確認窗口結束一律回 `status=error`、`kind=paused`',
                 '`error` 保留原碼', '沒有原碼時使用 `hid_failed`', '確認窗口即剩餘 D',
                 '即使回覆遺失亦算', '`start_test` 成功及監控就緒均不足以確認'):
        assert term in after
    assert 'data{pong, core_version, supported_commands[]}' in DOC
    assert 'StartRoundResp' in DOC and 'data{kind}' in DOC
    ping = DOC.split('### 9.1 `ping`', 1)[1].split('### 9.2', 1)[0]
    assert 'supported_commands' in ping and 'start_round' not in ping


def test_final_decisions_and_poll_budget():
    adr = (ROOT / 'docs/adr/0002-per-device-lock-and-fail-fast.md').read_text(encoding='utf-8')
    for doc in (SECTION, adr):
        for term in ('使用者 2026-11-17 定案', '110 秒', '硬上限', 'ICE 等待',
                     '145 秒不變', '不動共用連線池', '取消', '釋', 'T/3',
                     '最後一次使用當時剩餘的 T', '0.5 秒間隔計入同一 T／D',
                     '總等待不得超過 T', '只重讀畫面、繼續等待',
                     '不重新呼叫 `monitor_start`', '不再點 Dock',
                     '不再送快捷鍵（含 Cmd+Shift+M）', '監控中', '待命', '待確認',
                     '不得當成 `log_solution_busy`', '`error=log_solution_review`',
                     '`status=timeout`、`error=timeout`、`kind=fault`',
                     'D 截止或確認窗口結束一律回', 'hid_failed'):
            assert term in doc
        assert '暫定' not in doc
    assert 'KVM 連線與 ICE 等待算進這 110 秒' in SECTION
    assert 'LabVIEW TCP Read timeout 145 秒不變' in SECTION
    for term in ('待命：繼續只重讀畫面直到本次嘗試的 T 份額用盡',
                 '所有嘗試用盡仍為待命', '待確認：立即停止重試',
                 '監控中：本階段成功，進入後續開測流程',
                 '不可把 73 秒固定額度挪來當額外 T', '待 #213 實作時量測',
                 '結構化原因及階段紀錄', '不能重試所有 `hid_failed`',
                 '已送未確認必須 paused'):
        assert term in SECTION
    ping = DOC.split('### 5.1', 1)[1].split('### 5.2', 1)[0]
    for term in ('全量清單', '去重並排序', '使用者 2026-11-17 定案',
                 '其他名稱忽略', '不代表授權 LabVIEW 使用',
                 'pong=false', 'core_version=""', 'supported_commands=[]'):
        assert term in ping
