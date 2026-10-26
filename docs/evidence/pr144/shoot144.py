"""Usage: shoot144.py REPO LANG(en|zh) SCALE(100|200) KVM(offline|online) OUT.png [tip]"""
import sys, os, time
repo, lang, pct, kvm, out = sys.argv[1:6]
tip = len(sys.argv) > 6
sys.path.insert(0, os.path.join(repo, "tests")); sys.path.insert(0, os.path.join(repo, "host-app"))
from _pytest.monkeypatch import MonkeyPatch
import test_monitor_hmi_115 as T, ui_app, ui_i18n
from PIL import ImageGrab
mp = MonkeyPatch(); mp.setattr(ui_app.AtlasUI, "on_capture_snapshot", lambda self: None); mp.setattr(ui_app, "PatternCropper", object)
screen, scale = ((1920, 1080), 4 / 3) if pct == "100" else ((2880, 1800), 8 / 3)
T._simulate(mp, screen, scale)
a = ui_app.AtlasUI(core_host="127.0.0.1", core_port=1); a.client.start_status_poll = lambda cb: None
if ui_i18n.current_language() != lang: a.on_toggle_language()
rows = [
    {"device": "DFU-01", "station": "DFU", "status": "FROZEN", "is_frozen": True, "is_connected": kvm == "online"},
    {"device": "DFU-02", "station": "DFU", "status": "IDLE", "is_connected": True},
    {"device": "DFU-03", "station": "DFU", "status": "DISCONNECTED", "is_connected": False},
]
a._update_status_ui({"status": "ok", "stations": rows})
for _ in range(10): a.update(); time.sleep(0.04)
w = a._station_widgets["DFU-01"]
if tip:
    w["row_tip"].show()
    for _ in range(10): a.update(); time.sleep(0.04)
p = a.station_panel
x, y = p.winfo_rootx(), p.winfo_rooty()
h = 330 if tip else 230
ImageGrab.grab(bbox=(x, y, x + p.winfo_width(), y + h), xdisplay=os.environ["DISPLAY"]).save(out)
lamp = w["online_lamp"]; dot = w["lamp_dot"]
print(out, "fill=%r outline=%r text=%r status=%r bg=%r" % (lamp.itemcget(dot, "fill"), lamp.itemcget(dot, "outline"),
      w["online"].cget("text"), w["status_label"].cget("text"), w["card"].cget("bg")))
print("row_tip:", w["row_tip"].current_text().replace("\n", " | "))
a._is_closing = True; a._file_log.close(); a.destroy()
