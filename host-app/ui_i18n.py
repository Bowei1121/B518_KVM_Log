# -*- coding: utf-8 -*-
"""Monitor UI language: English by default, switched live with the 🌐 button (issue #106).

Every user-visible string lives in ``STRINGS`` under the same keys for ``en`` and ``zh``; a test
keeps the two tables in step. Widgets that show a fixed text register with ``bind`` and are re-labelled
when the language changes. Text that is rebuilt on every update is produced by ``t`` at that time.

The choice is saved in ``ui_prefs.json`` in the per-user settings folder, not beside the templates.
"""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from station_presentation import present_station

LANGUAGES = ("en", "zh")
DEFAULT_LANGUAGE = "en"
LANGUAGE_NAMES = {"en": "English", "zh": "中文"}

STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "station_connected": "Connected",
        "station_busy": "Busy",
        "station_offline": "Offline",
        "station_frozen": "Frame frozen",
        "station_heartbeat": "Heartbeat stopped",
        "station_heartbeat_not_found": "Heartbeat not found",
        "station_connect_failed": "Conn. failure",
        "station_retry_in": "Retry in {s}s ({n}/{total})",
        "station_retrying": "Retrying ({n}/{total})",
        "station_online_lamp": "Online",
        "station_offline_lamp": "Offline",
        "station_na_lamp": "N/A",
        # ---- main window: top bar
        "win_title": "ATE - LabVIEW Bridge Monitor v{version}",
        "top_title": "Core Service connection",
        "lbl_addr": "Service address:",
        "tip_reconnect": "Reconnect to the background service. Press this when the picture stops updating.",
        "tip_settings": "Add, change or remove the machines to watch and their KVM addresses.",
        "tip_lang": "Switch between 中文 and English.",
        "tip_addr": "Address of the Core Service (host:port). Normally leave it as it is.",
        "tip_match_device": "Which machine's last match results to show.",
        "tip_pt_choose": "Pick a screenshot file from disk to cut the template from.",
        "tip_pt_latest": "Use the picture you last took with the 📷 button.",
        "tip_pt_device": "Which machine this template belongs to.",
        "tip_pt_type": "Which step of the test this template recognises, for example the window.",
        "tip_pt_output": "The file the template is saved to. It is set automatically.",
        "tip_pt_cancel": "Close this window without saving.",
        "tip_pt_save": "Save the boxed area as the template. Drag a box on the picture first.",
        "core_caption": "Service",
        "core_ok_tip": "Service connected. Running for {seconds} s; {clients} program(s) connected.",
        "core_busy": "Busy machines: {busy}",
        "core_none": "none",
        "core_down": "Service not connected (see log)",
        # ---- station dashboard
        "dash_title": "Test machine status",
        "dash_empty": "No machines yet. Press ⚙ at the top right to add one.",
        "dash_offline": "Not connected to the service, so the machine list cannot be shown. Check the address above, or press 🔄.",
        # #140: two short lines in the list column (line 1 red); the sentence above is the hover tip, with these two
        "dash_offline_title": "⚠ Service offline",
        "dash_offline_action": "Press 🔄 to retry",
        "dash_offline_addr": "Address: {addr}",
        "dash_offline_call": "If retry fails, call the TE engineer.",
        "station_label": "Station: {station}",
        "row_tip_kvm_connected": "KVM: Connected",
        "row_tip_kvm_disconnected": "KVM: Disconnected",
        "row_tip_kvm_unknown": "KVM: Unknown (Core disconnected)",
        "light_idle": "Green: idle and ready to use.",
        "light_busy": "Yellow: testing now.",
        "light_disc": "Grey: offline (not connected).",
        "light_frozen": "Red: the picture is frozen (stuck).",
        "light_heartbeat": "Red: the heartbeat on the test PC screen stopped changing.",
        "light_heartbeat_not_found": "Red: heartbeat box not found. The Log Solution window may be covered, moved or minimized. Bring it to the front so the heartbeat box is visible; the alarm clears once the heartbeat is read again. If it stays red, notify an engineer.",
        "light_connect_failed": "Red, blinking: connection failure — 3 connection failures within 60 s. LabVIEW gets this device as abnormal from status and alarms the PLC (this screen cannot confirm the alarm). Core retries every 10 s and clears it on success. Notify an engineer; first check JetKVM power and network cable.",
        "light_retry": "Orange: connection failed; Core retries automatically, no action needed. 3 failures within 60 s = Connection failure.",
        "row_tip_conn_count": "Connection failures: {n} / {total}",
        "row_tip_first_fail": "First failure: {time}",
        "row_tip_last_error": "Last error: {code}",
        "row_tip_next_retry": "Next retry in {s}s",
        "row_tip_next_step": "Notify an engineer; first check JetKVM power and network cable",
        "tip_select": "Show this machine in the picture and controls below.",
        # ---- live picture
        "view_title": "Live view",
        "preview_none": "No picture yet",
        "tip_snapshot": "Take one picture of the machine now and show it in the preview.",
        "btn_live": "▶ Live view",
        "btn_live_stop": "⏹ Stop",
        "tip_live": "Keep playing the machine's picture like a monitor. Press again to stop.",
        "btn_popout": "⧉ Pop-out window",
        "tip_popout": "Open the picture in another large window, handy for a second screen.",
        "preview_info": "Size: {w}x{h} | Time: {time}",
        # ---- manual check and tools
        "ctrl_title": "Manual check and tools",
        "lbl_device": "Device ID:",
        "lbl_station": "Station:",
        "tip_device_combo": "Pick the machine to look at; the picture and controls follow it.",
        "check_title": "Manual check",
        "lbl_timeout": "Wait (s):",
        "tip_timeout": "Wait at most this many seconds. Longer than this counts as a failure.",
        "btn_check": "🩺 Check picture",
        "btn_checking": "⏳ Checking…",
        "tip_check": "Look at the picture once and work out which step the machine is on and whether anything is wrong.",
        "check_none": "Result: not run yet",
        "check_running": "Result: running…",
        "pat_title": "Template tools",
        "btn_pattern": "✂ Make template",
        "tip_pattern": "Crop a small piece of the picture to use as a template. Later the program looks for it on screen to tell which step the machine is on.",
        "btn_match": "📋 Match results",
        "tip_match": "See the last picture-vs-template comparison: which templates matched and their scores.",
        "pattern_unavailable": "Cannot make templates: the image library (OpenCV) is missing. See the log.",
        "log_title": "Log",
        "see_log": " (see log)",
        # ---- short error texts on the HMI
        "err_snapshot": "Snapshot failed",
        "err_decode": "Snapshot decode failed",
        "err_check": "Check failed",
        "err_timeout": "Check timed out",
        "err_busy": "Machine busy",
        "err_restart": "Restart failed",
        "err_conn_lost": "connection lost",
        "err_unknown": "unknown error",
        # ---- manual check result text
        "res_ok": "Result: {state}",
        "res_channel": "channel {slot} {evidence}",
        "res_elapsed": " (took {elapsed}s)",
        "res_timeout": "Result: TIMEOUT (took {elapsed}s)",
        "res_busy": "Result: BUSY (machine busy or LabVIEW test running)",
        "res_error": "Check error: {error}",
        "res_with_channels": "{result}; {channels}",
        "state_standby": "Standby",
        "state_preparing": "Preparing",
        "state_monitoring": "Ready to monitor",
        "state_review": "Needs review",
        "state_complete": "Completed",
        "state_manual_stop_complete": "Stopped by operator",
        "state_unknown": "unknown",
        "evidence_testing": "testing",
        "evidence_result": "has result",
        "sep_list": ", ",
        "sep_clause": "; ",
        # ---- station card metrics
        "rd_frame": "Frame {frame}  Scale {scale}  Cell {cell}",
        "rd_last": "Last read: {reason} ({age:.1f}s ago)",
        "rd_last_none": "Last read: —",
        "rd_reason_default": "readable",
        "rd_ratio": "Readable {ratio}% (last {samples})",
        "rd_ratio_none": "Readable — (0 samples)",
        # ---- log lines (file gets both halves; the panel shows the current one)
        "log_started": "Monitor UI client started.",
        "log_prefs_fail": "Could not save the language choice; it will reset to English on restart.",
        "log_target": "Core Service target: {addr}",
        "log_pattern_unavailable": "Cannot make templates: PatternCropper failed to load:\n{detail}",
        "log_connect_target": "Target changed to {addr}; connecting...",
        "log_core_down": "Core Service not connected: {error}",
        "log_snapshot_request": "Requesting snapshot from Core Service (device {device})...",
        "log_no_device_snapshot": "No device selected; cannot take a snapshot.",
        "log_snapshot_ok": "Snapshot received ({w}x{h})",
        "log_snapshot_decode": "Failed to decode the snapshot image: {error}",
        "log_snapshot_empty": "Snapshot data is empty",
        "log_snapshot_fail": "Snapshot failed: {error}",
        "log_stream_on": "Live view started (about 3 fps)...",
        "log_stream_off": "Live view stopped.",
        "log_streamer_missing": "Pop-out stream window module is unavailable.",
        "log_popout_open": "Pop-out stream window opened.",
        "log_popout_close": "Pop-out stream window closed.",
        "log_settings_missing": "Device settings module is unavailable.",
        "log_no_device_check": "No device selected; cannot run a manual check.",
        "log_inspect": "Sent manual inspect: device {device}, timeout {timeout}s...",
        "log_no_capture": "No picture captured yet. Press the 📷 button first.",
        "log_save_fail": "Failed to save the picture: {error}",
        "log_saved_capture": "Picture saved to the template folder: {path}",
        "log_need_snapshot": "Press the 📷 button first to get a picture.",
        "log_template_saved": "Template saved: {path}",
        "log_restart_fail": "Restarting Core Service failed: {detail}",
        "log_selected_device": "Selected machine {device} (station {station}) to watch.",
        "log_ui_callback_error": "UI callback error: {error}",
        "stream_title": "KVM Streamer - {device}",
        "streaming_hint": "Streaming (click the picture to control the remote machine; mouse move/click/wheel + keyboard)",
        # ---- match results window
        "match_title": "Latest match results",
        "match_device": "Device:",
        "match_none": "No match results yet",
        "match_none_hint": "Run a manual check or a test first.",
        "match_summary": "{operation}: {state}, {count} match step(s)",
        "match_done": "done",
        "match_failed": "failed",
        "match_hit": "Matched",
        "match_miss": "Missed",
        "match_pick": "Select a row on the left to see the overlay",
        "match_no_image": "No picture for this step",
        "match_read_fail": "Cannot read the overlay: {error}",
        "match_tip_list": "Each row: template, score (0 to 1), matched or missed.",
        # ---- device settings window
        "ds_title": "Devices",
        "ds_path": "Device list file: {path}",
        "ds_h_select": "Select",
        "ds_h_device": "Device ID",
        "ds_h_station": "Station",
        "ds_h_ip": "KVM IP",
        "ds_h_channels": "Channels",
        "ds_h_probe": "Connection test",
        "ds_add": "➕ Add",
        "ds_delete": "🗑 Delete selected",
        "ds_test_all": "🔌 Test all",
        "ds_revert": "↺ Revert",
        "ds_save": "💾 Save",
        "ds_restart": "🔄 Restart Core",
        "tip_ds_add": "Add a blank row for a new machine.",
        "tip_ds_delete": "Delete the ticked rows. Nothing is written until you press Save.",
        "tip_ds_test_all": "Test whether every machine's KVM address can be reached.",
        "tip_ds_revert": "Throw away unsaved changes and reload the file.",
        "tip_ds_save": "Save the list. Core must be restarted before the change takes effect.",
        "tip_ds_restart": "Restart the background service so the saved list takes effect. Refused while a machine is busy.",
        "tip_ds_select": "Tick to include this row in Delete selected.",
        "tip_ds_device": "The unique name of the machine, for example DFU-01.",
        "tip_ds_station": "Which station the machine belongs to.",
        "tip_ds_ip": "The IP address of this machine's JetKVM.",
        "tip_ds_channels": "How many test positions (slots) this machine has.",
        "tip_ds_probe": "Test whether this KVM address can be reached.",
        "ds_probe_btn": "Test",
        "ds_probing": "Testing…",
        "ds_prefilled": "No device list yet; pre-filled with the nine B518 machines (4 channels). Press Save to apply; Core must be restarted.",
        "ds_loaded": "Loaded {count} machine(s). After editing, press Save; Core must be restarted to apply.",
        "ds_nothing_selected": "No rows are ticked for deleting.",
        "ds_deleted": "Deleted {count} row(s) (not saved yet).",
        "ds_reverted": "Reverted to the file contents.",
        "ds_has_errors": "Not saved: {count} row(s) have errors. Fix the hints shown in each row.",
        "ds_not_saved": "Not saved: {error}",
        "ds_saved_backup": "Saved (backup: {name}). Core must be restarted to apply.",
        "ds_saved": "Saved. Core must be restarted to apply.",
        "ds_probe_started": "Started connection tests for {count} machine(s).",
        "ds_dirty": "Unsaved changes. Save first, then restart Core.",
        "ds_checking": "Checking machine status…",
        "ds_restarting": "Restarting Core…",
        "ds_no_restart": "This window has no restart function.",
        "ds_restarted": "Core restarted.",
        # ---- device list rules (device_list_editor)
        "ed_read_fail": "Cannot read device list {path}: {error}",
        "ed_not_object": "The device list must be a JSON object keyed by device ID.",
        "ed_empty": "The device list is empty.",
        "ed_id_empty": "Device ID cannot be empty.",
        "ed_id_dup": "Duplicate device ID (same as row {row}).",
        "ed_refused": "Device list has errors; nothing was written.",
        "ed_probe_timeout": "Timed out ({seconds:g} s)",
        "ed_probe_fail": "Cannot connect: {error}",
        "ed_probe_ok": "Connected, {ms} ms",
        "ed_restart_busy": "These machines are busy; cannot restart Core: {names}",
        # ---- template cropper window
        "pt_title": "Make JetKVM template",
        "pt_choose": "Choose screenshot",
        "pt_latest": "Use latest picture",
        "pt_device": "Device",
        "pt_type": "Template type",
        "pt_output": "Output file",
        "pt_cancel": "Cancel",
        "pt_save": "Save template",
        "pt_hint_start": "Choose a screenshot, then drag a box around the template area.",
        "pt_dialog_title": "Choose JetKVM screenshot",
        "pt_file_images": "Image files",
        "pt_file_all": "All files",
        "pt_load_title": "Cannot load image",
        "pt_load_fail": "Cannot read image: {path}",
        "pt_loaded": "Loaded {name}; drag a box around the template area.",
        "pt_selected": "Selected {w} x {h} px; press Save template to write the file.",
        "pt_overwrite_title": "Overwrite template?",
        "pt_overwrite_msg": "The target file already exists. Overwrite it?\n\n{path}",
        "pt_overwrite_cancel": "Overwrite cancelled; the original template is unchanged.",
        "pt_save_title": "Save failed",
        "pt_no_image_title": "No image loaded",
        "pt_no_image_msg": "Choose a screenshot or use the latest picture first.",
        "pt_no_selection_title": "Nothing selected",
        "pt_no_selection_msg": "Drag a box around the template area with the mouse.",
        "pt_saved": "Template saved: {path}",
        "pt_more": "; you can make another type now.",
        "pt_saved_title": "Template saved",
        # Station list scroll hint and log (spec v5.3 §14.10, §14.12; issue #126)
        "list_more_below": "▼ {n} more below",
        "list_more_above": "▲ {n} more above",
        "list_more_down": "▼ {n} more",
        "list_more_up": "▲ {n} more",
        "list_more_both": "▲ {a} ▼ {b} more",
        "list_abnormal": " · {k} abnormal ({ids})",
        "list_abnormal_only": "{arrow} {k} abnormal ({ids})",
        "list_abnormal_both": "{ids} abnormal",
        "list_abnormal_both_count": "{k} abnormal",
        "log_list_scroll": "{n} devices configured; list shows 12 rows, scroll for the rest",
        # Alarm banner (spec v5.3 §13, §14.4, §14.12; issue #127)
        "alarm_devices": "{n} devices abnormal",
        "alarm_connect_failed": "Connection failure",
        "alarm_hint_frozen": "Check JetKVM",
        "alarm_hint_heartbeat": "Check test PC and Log Solution",
        "alarm_hint_heartbeat_not_found": "Check Log Solution window is visible",
        "alarm_list_cat": "{icon} {kind}: {ids}",
        "alarm_more": "… +{n} more",
        "alarm_hint_more": "… +{n} more",
        "alarm_up_one": "▲ {ids}: scroll up to see it",
        "alarm_up_many": "▲ {ids}: scroll up to see them",
        "alarm_down_one": "▼ {ids}: scroll down to see it",
        "alarm_down_many": "▼ {ids}: scroll down to see them",
        "alarm_scroll_both": "▲▼ {ids}: scroll to see them",
        "alarm_scroll_count": "▲▼ {n} abnormal: scroll to see",
        "alarm_up_count": "▲ {n} more above: scroll up",
        "alarm_down_count": "▼ {n} more below: scroll down",
        "alarm_up_short": "▲ {n} more above",
        "alarm_down_short": "▼ {n} more below",
        "alarm_scroll_short": "▲ {a} above　▼ {b} below",
        "log_alarm_shown": "Alarm banner shown ({n} abnormal)",
        "log_alarm_cleared": "All abnormal states cleared; alarm banner hidden",
    },
    "zh": {
        "station_connected": "已連線",
        "station_busy": "執行指令中",
        "station_offline": "未連線",
        "station_frozen": "畫面凍結",
        "station_heartbeat": "心跳停止",
        "station_heartbeat_not_found": "找不到心跳框",
        "station_connect_failed": "連線失敗異常",
        "station_retry_in": "{s} 秒後重試（{n}/{total}）",
        "station_retrying": "重試中（{n}/{total}）",
        "station_online_lamp": "已連線",
        "station_offline_lamp": "未連線",
        "station_na_lamp": "未知",
        # ---- main window: top bar
        "win_title": "ATE - LabVIEW Bridge Monitor v{version}",
        "top_title": "Core Service 連線設定",
        "lbl_addr": "服務位址：",
        "tip_reconnect": "重新連上背景服務。畫面資料不更新的時候按這個。",
        "tip_settings": "新增、修改要監看的機台和它的 KVM 位址。",
        "tip_lang": "切換中文 / English。",
        "tip_addr": "背景服務的位址（主機:埠號），通常不用改。",
        "tip_match_device": "要查看哪一台機台上一次的比對結果。",
        "tip_pt_choose": "從電腦選一張截圖，用來裁出模板。",
        "tip_pt_latest": "用上次按 📷 擷取的畫面。",
        "tip_pt_device": "這個模板屬於哪一台機台。",
        "tip_pt_type": "這個模板用來辨識測試的哪一步，例如視窗。",
        "tip_pt_output": "模板會存到這個檔案，自動決定，不用改。",
        "tip_pt_cancel": "不儲存，直接關閉視窗。",
        "tip_pt_save": "把框選的範圍存成模板。請先在畫面上拖曳框選。",
        "core_caption": "服務",
        "core_ok_tip": "服務連線正常。已連線 {seconds} 秒，目前有 {clients} 個程式在用。",
        "core_busy": "忙碌機台：{busy}",
        "core_none": "無",
        "core_down": "服務未連線（詳見日誌）",
        # ---- station dashboard
        "dash_title": "測試機台即時狀態",
        "dash_empty": "還沒有設定機台，請按右上角 ⚙ 新增。",
        "dash_offline": "尚未連上服務，無法顯示機台清單。請檢查上方位址，或按 🔄 重連。",
        "dash_offline_title": "⚠ 未連上服務",
        "dash_offline_action": "請按 🔄 重試",
        "dash_offline_addr": "位址：{addr}",
        "dash_offline_call": "重試仍失敗，請通知 TE 工程師。",
        "station_label": "站別：{station}",
        "row_tip_kvm_connected": "KVM：已連線",
        "row_tip_kvm_disconnected": "KVM：已斷線",
        "row_tip_kvm_unknown": "KVM：未知（Core 未連線）",
        "light_idle": "綠燈：閒置，可以使用。",
        "light_busy": "黃燈：測試中。",
        "light_disc": "灰燈：未連線。",
        "light_frozen": "紅燈：畫面卡住不動。",
        "light_heartbeat": "紅燈：測試電腦畫面上的心跳停止變化。",
        "light_heartbeat_not_found": "紅燈：找不到心跳框。Log Solution 視窗可能被遮住、移走或縮小。請把它拉到最上層，讓心跳框露出來；重新讀到心跳就會自動解除。還是紅色就請通知工程師。",
        "light_connect_failed": "紅色閃爍：連線失敗異常，60 秒內連續 3 次連線失敗。LabVIEW 查詢狀態時會收到此設備異常並通知 PLC 報警（本畫面無法確認是否已報警）。Core 每 10 秒自動重試，連線成功即自動解除。請通知工程師；可先檢查 JetKVM 電源和網路線。",
        "light_retry": "橘燈：連線失敗，Core 會自動重試，無需操作。60 秒內連續 3 次失敗才判定連線失敗異常。",
        "row_tip_conn_count": "連續失敗 {n} / {total}",
        "row_tip_first_fail": "首次失敗 {time}",
        "row_tip_last_error": "最後錯誤 {code}",
        "row_tip_next_retry": "下次重試 {s} 秒後",
        "row_tip_next_step": "請通知工程師；可先檢查 JetKVM 電源和網路線",
        "tip_select": "把下方的畫面和操作切換到這台機台。",
        # ---- live picture
        "view_title": "即時畫面監控",
        "preview_none": "尚未擷取影格",
        "tip_snapshot": "抓一張機台現在的畫面，顯示在預覽區。",
        "btn_live": "▶ 即時畫面",
        "btn_live_stop": "⏹ 停止",
        "tip_live": "持續播放機台畫面，像看監視器一樣。再按一次就停止。",
        "btn_popout": "⧉ 放大視窗",
        "tip_popout": "把畫面開在另一個大視窗，方便放到第二個螢幕上看。",
        "preview_info": "解析度：{w}x{h} | 時間：{time}",
        # ---- manual check and tools
        "ctrl_title": "手動診斷與操作",
        "lbl_device": "設備 ID：",
        "lbl_station": "站別：",
        "tip_device_combo": "選擇要查看的機台，畫面與操作會跟著切換。",
        "check_title": "手動檢查",
        "lbl_timeout": "等待秒數：",
        "tip_timeout": "最多等幾秒。超過就算失敗。",
        "btn_check": "🩺 檢查畫面",
        "btn_checking": "⏳ 檢查中…",
        "tip_check": "讓程式看一眼目前畫面，判斷機台跑到哪一步、有沒有異常。",
        "check_none": "檢查結果：尚未執行",
        "check_running": "檢查結果：執行中…",
        "pat_title": "模板工具",
        "btn_pattern": "✂ 製作模板",
        "tip_pattern": "從畫面裁一小塊當「範本」，之後程式會找畫面上有沒有出現這塊，用來判斷機台跑到哪一步。",
        "btn_match": "📋 比對結果",
        "tip_match": "看上一次「畫面和範本比對」的結果：哪些範本有對到、相似度幾分。",
        "pattern_unavailable": "無法製作模板：缺少影像處理元件（OpenCV）。詳見日誌。",
        "log_title": "系統與操作日誌",
        "see_log": "（詳見日誌）",
        # ---- short error texts on the HMI
        "err_snapshot": "快照失敗",
        "err_decode": "快照解碼失敗",
        "err_check": "診斷失敗",
        "err_timeout": "診斷逾時",
        "err_busy": "設備忙碌中",
        "err_restart": "重啟失敗",
        "err_conn_lost": "連線中斷",
        "err_unknown": "未知錯誤",
        # ---- manual check result text
        "res_ok": "診斷結果：{state}",
        "res_channel": "通道 {slot} {evidence}",
        "res_elapsed": " (耗時: {elapsed}s)",
        "res_timeout": "診斷結果：TIMEOUT（耗時 {elapsed}s）",
        "res_busy": "診斷結果：BUSY（設備忙碌中／LabVIEW 測試中）",
        "res_error": "診斷錯誤：{error}",
        "res_with_channels": "{result}；{channels}",
        "state_standby": "待命",
        "state_preparing": "準備中",
        "state_monitoring": "監控就緒",
        "state_review": "待確認",
        "state_complete": "一般完成",
        "state_manual_stop_complete": "人工停止完成",
        "state_unknown": "未知",
        "evidence_testing": "測試中",
        "evidence_result": "已有結果",
        "sep_list": "、",
        "sep_clause": "；",
        # ---- station card metrics
        "rd_frame": "影格 {frame}  換算 {scale}  小格 {cell}",
        "rd_last": "最近讀取 {reason}（{age:.1f}s 前）",
        "rd_last_none": "最近讀取 —",
        "rd_reason_default": "可讀",
        "rd_ratio": "可讀率 {ratio}%（最近 {samples} 次）",
        "rd_ratio_none": "可讀率 —（0 次）",
        # ---- log lines (file gets both halves; the panel shows the current one)
        "log_started": "Monitor UI 用戶端啟動完成。",
        "log_prefs_fail": "無法儲存語言設定，重新開啟後會回到英文。",
        "log_target": "連線目標 Core Service：{addr}",
        "log_pattern_unavailable": "製作模板功能不可用，PatternCropper 載入失敗：\n{detail}",
        "log_connect_target": "更新目標連線位址至 {addr}，嘗試連線…",
        "log_core_down": "Core Service 未連線：{error}",
        "log_snapshot_request": "向 Core Service 請求快照（設備 {device}）…",
        "log_no_device_snapshot": "尚未選定設備，無法擷取快照。",
        "log_snapshot_ok": "快照接收成功（{w}x{h}）",
        "log_snapshot_decode": "解碼快照影像失敗：{error}",
        "log_snapshot_empty": "快照資料為空",
        "log_snapshot_fail": "擷取快照失敗：{error}",
        "log_stream_on": "開始即時畫面（約 3 fps）…",
        "log_stream_off": "已停止即時畫面。",
        "log_streamer_missing": "獨立串流視窗模組不可用。",
        "log_popout_open": "已開啟獨立串流視窗。",
        "log_popout_close": "已關閉獨立串流視窗。",
        "log_settings_missing": "設備設定頁模組不可用。",
        "log_no_device_check": "尚未選定設備，無法執行手動檢查。",
        "log_inspect": "手動送出 inspect 指令：設備 {device}，逾時 {timeout} 秒…",
        "log_no_capture": "尚未擷取影格，請先點擊「📷」擷取快照。",
        "log_save_fail": "儲存影格失敗：{error}",
        "log_saved_capture": "已儲存影格至模板目錄：{path}",
        "log_need_snapshot": "請先按「📷」擷取快照取得影格。",
        "log_template_saved": "已儲存模板：{path}",
        "log_restart_fail": "重啟 Core Service 失敗：{detail}",
        "log_selected_device": "已選定觀測設備：{device}（站別 {station}）",
        "log_ui_callback_error": "UI 回呼錯誤：{error}",
        "stream_title": "KVM 串流 - {device}",
        "streaming_hint": "串流中 (點畫面操作遠端; 滑鼠移動/點擊/滾輪 + 鍵盤)",
        # ---- match results window
        "match_title": "最近一次匹配結果",
        "match_device": "設備：",
        "match_none": "尚無最近一次診斷結果",
        "match_none_hint": "請先執行手動檢查或測試操作後再查看。",
        "match_summary": "{operation}：{state}，{count} 個匹配步驟",
        "match_done": "完成",
        "match_failed": "失敗",
        "match_hit": "命中",
        "match_miss": "未命中",
        "match_pick": "選擇左側項目以查看疊圖",
        "match_no_image": "此步沒有可顯示的影格",
        "match_read_fail": "無法讀取疊圖：{error}",
        "match_tip_list": "每列：範本名稱、相似度（0 到 1）、命中或未命中。",
        # ---- device settings window
        "ds_title": "設備設定",
        "ds_path": "設備清單檔：{path}",
        "ds_h_select": "選取",
        "ds_h_device": "設備 ID",
        "ds_h_station": "站別",
        "ds_h_ip": "KVM IP",
        "ds_h_channels": "測試通道數",
        "ds_h_probe": "連線測試",
        "ds_add": "➕ 新增設備",
        "ds_delete": "🗑 刪除選取",
        "ds_test_all": "🔌 全部測試",
        "ds_revert": "↺ 還原",
        "ds_save": "💾 儲存",
        "ds_restart": "🔄 重啟 Core",
        "tip_ds_add": "新增一列空白，填入新機台的資料。",
        "tip_ds_delete": "刪除打勾的列。按「儲存」前不會寫入檔案。",
        "tip_ds_test_all": "逐台測試 KVM 位址能不能連上。",
        "tip_ds_revert": "放棄還沒儲存的修改，重新載入檔案內容。",
        "tip_ds_save": "儲存設備清單。需重啟 Core 才會生效。",
        "tip_ds_restart": "重啟背景服務，讓儲存的清單生效；有機台忙碌時會拒絕。",
        "tip_ds_select": "勾選後，這一列會被「刪除選取」刪掉。",
        "tip_ds_device": "機台的唯一名稱，例如 DFU-01。",
        "tip_ds_station": "這台機台屬於哪個站別。",
        "tip_ds_ip": "這台機台的 JetKVM IP 位址。",
        "tip_ds_channels": "這台機台有幾個測試位置（通道數）。",
        "tip_ds_probe": "測試這個 KVM 位址能不能連上。",
        "ds_probe_btn": "測試",
        "ds_probing": "測試中…",
        "ds_prefilled": "尚無設備清單，已預填 B518 九台（通道數 4）。按「儲存」後生效，需重啟 Core。",
        "ds_loaded": "已載入 {count} 台設備。修改後按「儲存」，需重啟 Core 才會生效。",
        "ds_nothing_selected": "沒有勾選要刪除的列。",
        "ds_deleted": "已刪除 {count} 列（尚未儲存）。",
        "ds_reverted": "已還原為檔案內容。",
        "ds_has_errors": "未儲存：{count} 列有錯誤，請依列內提示修正。",
        "ds_not_saved": "未儲存：{error}",
        "ds_saved_backup": "已儲存（備份：{name}）。需重啟 Core 才會生效。",
        "ds_saved": "已儲存。需重啟 Core 才會生效。",
        "ds_probe_started": "已送出 {count} 台連線測試。",
        "ds_dirty": "有未儲存的修改。請先儲存，再重啟 Core。",
        "ds_checking": "檢查設備狀態…",
        "ds_restarting": "重啟 Core 中…",
        "ds_no_restart": "此視窗未提供重啟功能。",
        "ds_restarted": "Core 已重啟。",
        # ---- device list rules (device_list_editor)
        "ed_read_fail": "無法讀取設備清單 {path}：{error}",
        "ed_not_object": "設備清單必須是以設備 ID 為 key 的 JSON 物件",
        "ed_empty": "設備清單不可為空",
        "ed_id_empty": "設備 ID 不可為空",
        "ed_id_dup": "設備 ID 重複（與第 {row} 列相同）",
        "ed_refused": "設備清單有錯誤，未寫入檔案",
        "ed_probe_timeout": "逾時（{seconds:g} 秒）",
        "ed_probe_fail": "無法連線：{error}",
        "ed_probe_ok": "連線成功 {ms} ms",
        "ed_restart_busy": "以下設備正在執行動作，不能重啟 Core：{names}",
        # ---- template cropper window
        "pt_title": "製作 JetKVM 模板",
        "pt_choose": "選擇截圖",
        "pt_latest": "使用最新擷取",
        "pt_device": "設備",
        "pt_type": "模板種類",
        "pt_output": "輸出檔案",
        "pt_cancel": "取消",
        "pt_save": "儲存模板",
        "pt_hint_start": "選擇截圖後，以滑鼠拖曳框選模板區域。",
        "pt_dialog_title": "選擇 JetKVM 截圖",
        "pt_file_images": "圖片檔",
        "pt_file_all": "所有檔案",
        "pt_load_title": "無法載入圖片",
        "pt_load_fail": "讀不到影像：{path}",
        "pt_loaded": "已載入 {name}；拖曳框選模板區域。",
        "pt_selected": "已框選 {w} x {h} 像素；按「儲存模板」寫入指定檔案。",
        "pt_overwrite_title": "確認覆寫模板",
        "pt_overwrite_msg": "目標檔案已存在，是否覆寫？\n\n{path}",
        "pt_overwrite_cancel": "已取消覆寫，原模板未變更。",
        "pt_save_title": "儲存失敗",
        "pt_no_image_title": "尚未載入圖片",
        "pt_no_image_msg": "請先選擇截圖或使用最新擷取。",
        "pt_no_selection_title": "尚未框選",
        "pt_no_selection_msg": "請以滑鼠拖曳框選模板區域。",
        "pt_saved": "已儲存模板：{path}",
        "pt_more": "；可繼續選擇其他種類製作。",
        "pt_saved_title": "模板已儲存",
        # Station list scroll hint and log (spec v5.3 §14.10, §14.12; issue #126)
        "list_more_below": "▼ 下方還有 {n} 台",
        "list_more_above": "▲ 上方還有 {n} 台",
        "list_more_down": "▼ 還有 {n} 台",
        "list_more_up": "▲ 還有 {n} 台",
        "list_more_both": "▲ {a} 台 ▼ {b} 台",
        "list_abnormal": " · {k} 台異常（{ids}）",
        "list_abnormal_only": "{arrow} {k} 台異常（{ids}）",
        "list_abnormal_both": "{ids} 異常",
        "list_abnormal_both_count": "{k} 台異常",
        "log_list_scroll": "設定了 {n} 台設備；清單一次顯示 12 列，其餘請用捲軸查看",
        # Alarm banner (spec v5.3 §13, §14.4, §14.12; issue #127)
        "alarm_devices": "{n} 台設備異常",
        "alarm_connect_failed": "連線失敗異常",
        "alarm_hint_frozen": "請檢查 JetKVM",
        "alarm_hint_heartbeat": "請檢查測試電腦和 Log Solution",
        "alarm_hint_heartbeat_not_found": "請確認 Log Solution 視窗沒有被遮住",
        "alarm_list_cat": "{icon} {kind}：{ids}",
        "alarm_more": "…另 {n} 台",
        "alarm_hint_more": "…另 {n} 項",
        "alarm_up_one": "▲ {ids}：請往上捲動查看",
        "alarm_up_many": "▲ {ids}：請往上捲動查看",
        "alarm_down_one": "▼ {ids}：請往下捲動查看",
        "alarm_down_many": "▼ {ids}：請往下捲動查看",
        "alarm_scroll_both": "▲▼ {ids}：請捲動查看",
        "alarm_scroll_count": "▲▼ {n} 台：請捲動查看",
        "alarm_up_count": "▲ 上方 {n} 台異常：請往上捲動查看",
        "alarm_down_count": "▼ 下方 {n} 台異常：請往下捲動查看",
        "alarm_up_short": "▲ 上方 {n} 台異常",
        "alarm_down_short": "▼ 下方 {n} 台異常",
        "alarm_scroll_short": "▲ 上方 {a} 台　▼ 下方 {b} 台",
        "log_alarm_shown": "顯示警示橫幅（{n} 台異常）",
        "log_alarm_cleared": "所有異常已解除，警示橫幅已隱藏",
    },
}


_current = DEFAULT_LANGUAGE
_listeners: List[Callable[[], None]] = []
# (widget, option, render): render() gives the text in the current language; re-run on every switch.
_bound: List[Tuple[Any, str, Callable[[], str]]] = []


# ---------------------------------------------------------------- text lookup

def current_language() -> str:
    return _current


def t(key: str, lang: Optional[str] = None, **kwargs: Any) -> str:
    """Text for ``key`` in ``lang`` (default: the current language). Falls back to English, then to the key."""
    table = STRINGS.get(lang or _current, STRINGS[DEFAULT_LANGUAGE])
    text = table.get(key)
    if text is None:
        text = STRINGS[DEFAULT_LANGUAGE].get(key, key)
    return text.format(**kwargs) if kwargs else text


def station_status_text(pres: Any, seconds_left: int = 0, lang: Optional[str] = None) -> str:
    """Status words of a row; the Retry words carry the attempt count and the seconds left (issue #134)."""
    return t(pres.status_key, lang, s=max(0, int(seconds_left)), **pres.status_args)


def pair(key: str, **kwargs: Any) -> Tuple[str, str]:
    """``(English, 中文)`` for one message: what the Log file records on each line."""
    return t(key, lang="en", **kwargs), t(key, lang="zh", **kwargs)


def station_light_tip_key(status: str) -> str:
    """Hover text key for a station light, matched the same way as ``ui_widgets.light_for_status``."""
    return present_station({"status": status}).text_key


# ---------------------------------------------------------------- live switching

def on_language_change(callback: Callable[[], None]) -> None:
    """Run ``callback`` after every ``set_language``, for text the registry cannot re-label by itself."""
    _listeners.append(callback)


def off_language_change(callback: Callable[[], None]) -> None:
    """Stop calling ``callback``. Call it when the window that owns it closes."""
    if callback in _listeners:
        _listeners.remove(callback)


def _alive(widget: Any) -> bool:
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


def bind(widget: Any, key_or_render: Union[str, Callable[[], str]], option: str = "text", **kwargs: Any) -> Any:
    """Show a text in ``widget`` now and again whenever the language changes.

    ``key_or_render`` is a ``STRINGS`` key (with ``kwargs``) or a callable that returns the text in the current
    language. ``option="title"`` sets a window title. A later ``bind`` on the same widget and option replaces
    the earlier one, so a label that shows a new message each time never keeps stale entries.
    """
    if callable(key_or_render):
        render = key_or_render
    else:
        key = key_or_render

        def render() -> str:
            return t(key, **kwargs)

    _bound[:] = [entry for entry in _bound if _alive(entry[0]) and not (entry[0] is widget and entry[1] == option)]
    _bound.append((widget, option, render))
    _apply(_bound[-1])
    return widget


def _apply(entry: Tuple[Any, str, Callable[[], str]]) -> None:
    widget, option, render = entry
    text = render()
    if option == "title":
        widget.title(text)
    else:
        widget.configure(**{option: text})


def set_language(lang: str) -> None:
    """Switch the whole UI now. Bound widgets are re-labelled; destroyed windows are dropped."""
    global _current
    if lang not in STRINGS:
        raise ValueError(f"unknown UI language: {lang!r}")
    _current = lang
    _bound[:] = [entry for entry in _bound if _alive(entry[0])]
    for entry in list(_bound):
        try:
            _apply(entry)
        except tk.TclError:
            _bound.remove(entry)
    for callback in list(_listeners):
        try:
            callback()
        except tk.TclError:  # a window that was destroyed without being removed
            _listeners.remove(callback)


# ---------------------------------------------------------------- saved preference

def prefs_path() -> Path:
    """``%APPDATA%\\AtlasKVM\\ui_prefs.json`` on Windows, ``~/Library/Application Support/AtlasKVM`` on macOS."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AtlasKVM" / "ui_prefs.json"
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "AtlasKVM" / "ui_prefs.json"
    return Path.home() / ".config" / "AtlasKVM" / "ui_prefs.json"


def load_saved_language() -> str:
    """The language saved last time, or English when there is none or the file cannot be read."""
    try:
        data = json.loads(prefs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_LANGUAGE
    lang = data.get("language") if isinstance(data, dict) else None
    return lang if lang in STRINGS else DEFAULT_LANGUAGE


def save_language(lang: str) -> bool:
    """Remember ``lang`` for the next start. Returns False when the file cannot be written; the UI still switches."""
    path = prefs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"language": lang}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    return True
