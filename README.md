# B518 JetKVM + Log

此 repo 是 B518 的 JetKVM + Log prototype，獨立於 Arduino + Log 方案。

## 目錄

- `host-app/`：上位機、自動化與打包原始碼。
- `config/`：站別與 KVM 設定範例。
- `docs/`：研究、架構與驗證紀錄。
- `tests/`：方案測試說明與未來測試。
- `third_party/jetkvm/`：固定版本的 JetKVM private mirror submodule。

## 取得原始碼

```sh
git clone --recurse-submodules <repo-url>
```

既有 clone 請執行 `git submodule update --init --recursive`。

`third_party/jetkvm` 的 `origin` 為公司 private mirror，`upstream` 為 JetKVM 官方 repo。修改第三方程式碼前請先確認 GPL-2.0 的散布與授權義務。
