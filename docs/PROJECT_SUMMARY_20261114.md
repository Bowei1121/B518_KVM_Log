# 專案摘要（2026-11-14）

本次可見對話聚焦 #204：Core 背景工作 fail-fast 與可選 watchdog，延續 #166／#168 Task Scheduler 與 core_uptime_sec。未提供更早對話內容，不推測歷史決策。

本日後續修補：Core rotating log 恢復 `HH:MM:SS | message | message` 與 ASCII 英文內容，格式／watchdog 聚焦測試 8 通過。以 `/workspace/venv53/bin/python` 執行指定完整測試得到 63 通過、22 失敗；目前沙箱禁止 TCP socket，造成 EPERM 與衍生 timeout，仍需在可開 socket 的環境驗證僅剩 tkinter baseline。最新驗收證據見 `/tmp/pr204.md`；持續依使用者指示不提交、不推送、不操作 issue。

使用者要求測試先行、禁止提交／推送／PR／議題寫入，保留工作目錄供人工提交；禁止 Windows Service 與顯示契約變更。實作與測試草稿已完成，完整 pytest 因環境缺少依賴與 DNS 失敗尚未執行。詳細證據與未驗項目見 `/tmp/pr204.md` 及 `docs/retros/2026-11-14-issue204.md`。
