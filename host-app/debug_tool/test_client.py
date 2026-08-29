# -*- coding: utf-8 -*-
"""
TCP 測試用戶端: 連到 ui_app 的伺服器, 送一個字串, 印出伺服器回覆。

用法:
  1. 先在 ui_app 按「打開服務器」(預設 Port 8888)
  2. 執行:  python test_client.py            (送預設字串)
            python test_client.py SN_12345   (送指定字串)
            python test_client.py SN_12345 9000   (指定字串與 Port)
"""
import socket
import sys

HOST = "127.0.0.1"
msg = sys.argv[1] if len(sys.argv) > 1 else "Hello_From_Client"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8888

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.settimeout(3)
    s.connect((HOST, port))
    s.sendall(msg.encode("utf-8"))
    print(f"已送出: {msg}")
    reply = s.recv(4096)
    print(f"伺服器回覆: {reply.decode('utf-8', errors='replace')}")
