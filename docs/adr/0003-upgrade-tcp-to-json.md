# 3. 升級 TCP 協定為 JSON-RPC 並放棄向下相容

Date: 2026-10-06

## Status
Accepted

## Context
原有的通訊格式為逗號分隔純字串（例如 `DFU,1,192.168.1.10,input,1:SN123,3:SN789`）。這種格式在解析 SN 內容（可能包含逗號、冒號或特殊符號）時極易崩潰，且缺乏 Request ID 無法做精確的回覆配對，也沒有結構化的錯誤資訊欄位。

## Decision
我們決定徹底放棄舊版逗號字串協定，不再維護向下相容。
中繼站將全面改為接收及發送**換行符分隔的 JSON（JSON-RPC 2.0-like）**格式：
- 必須包含 `req_id`。
- 指令、目標與參數明確分開（`cmd`, `device`, `kvm_ip`, `payload`）。
- 回傳時一併帶上執行狀態、錯誤細節及耗時。

## Consequences
- **Pros**: 大幅提高通訊穩定度與擴充性，資料跳脫問題自動由 JSON 解析器處理。LabVIEW 有現成的 JSON 解析 VI 可以輕易串接。
- **Cons**: LabVIEW 端的網路模組必須跟著改寫。
