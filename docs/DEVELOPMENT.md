# Flash32 开发文档

写于 2026-10-17，对应 v0.4.0。记录项目现状、代码结构、关键设计和踩过的坑，方便以后接着做。使用说明见 [README](../README.md)。

## 1. 定位与现状

Flash32 是给不用命令行的人用的通用 ESP32 刷机工具：插上板子 → 自动识别芯片 → 只列出适合这块板子的开源固件 → 一键刷入。打包后自带 Python 和 esptool，用户什么都不用装。

| 版本 | 内容 |
|---|---|
| 0.1.x | 首个版本：固件目录（仅 Muse）、网址/本地文件、刷写、Muse token 设置、串口日志 |
| 0.2.0 | 首刷前自动备份整片 Flash、还原备份；插拔自动识别串口；读出板子上现有固件 |
| 0.3.0 | 以芯片为中心：目录加入 WLED、Tasmota、ESPHome、MicroPython、立创 fork；按 Flash/PSRAM 自动选版本 |
| 0.4.0 | 软件内自动更新（GitHub Release + SHA-256 校验） |

短期没有计划中的改动。

相关仓库：

- 本仓库：<https://github.com/rw0104/Flash32>（`main` 分支，Apache-2.0）
- 立创·实战派 ESP32-S3 的 Muse 固件：<https://github.com/rw0104/muse-gadget-sdk>（默认分支 `lckfb-szpi-esp32-s3`，Release 附带 `manifest.json` 和合并镜像，含千问语音播报，语速默认 1.1，串口发送 `>tts_rate=1.0` 恢复原速）
- 上游通用修复 PR：facebookincubator/muse-gadget-sdk#231（输入框布局；等 Meta CLA 和维护者审核）。立创板级支持和千问 TTS 只放在 fork，不提给上游。

## 2. 开发环境

```bash
python -m pip install -r requirements.txt   # PySide6 6.11、esptool 5.1、pyserial 3.5
python app.py                               # 运行
python -m unittest discover -s tests -t .   # 测试（38 个，约 5 秒）
```

打包（Windows）：`.\build.ps1` 会建独立的 `.venv-build`，用 PyInstaller 6.13 产出 `dist\Flash32\`（onedir，约 87 MB）。脚本会剔除用不到的 Qt 模块，并检查没有误打包外部的 ICU DLL。

`app.py --screenshot out.png` 渲染 2.5 秒后截图退出（注意：插着板子时可能正好打断自动识别，板子会停在下载模式，重新插线或用 esptool `read-mac` 复位即可）。

## 3. 代码结构

```
app.py                  入口；--run-esptool 让打包后的 exe 自己充当 esptool
data/catalog.json       内置固件目录
flasher/
  window.py             主窗口（Qt），所有界面逻辑
  workers.py            后台任务（QThread）：识别、刷写、备份、还原、目录、更新……
  esptool_jobs.py       esptool 命令行拼装、输出解析（芯片/Flash/MAC/PSRAM、进度）
  manifest.py           ESP Web Tools 清单解析、build 选择、模板填充、分区表和 app 描述解析
  catalog.py            固件目录：来源、选项、在线列表、按芯片匹配
  backups.py            整片 Flash 备份的存储与元数据
  updater.py            自更新：查 Release、下载校验、解压、PowerShell 替换脚本
  devices.py            串口列举、识别常见 USB 转串口芯片
  provision.py          刷完后通过串口写设置（Muse token 等），日志脱敏
  i18n.py               中英文字符串（键在两种语言中必须一致，有测试保证）
  styles.py             Qt 样式表
tools/export_manifest.py  把 ESP-IDF build 目录导出成固件文件夹 + manifest.json（开发者用）
tests/test_core.py      单元测试（不需要硬件）
```

### 后台任务约定

所有耗时操作都是 `workers.Job` 子类，通过信号汇报：`log`（已脱敏的日志行）、`progress`（0..1，-1 表示忙但无进度）、`stage`（i18n 键）、`done(ok, key, detail, result)`。失败用 `JobFailed(key, detail, result)` 或 `ManifestError` / `ProvisionError` / `UpdateError` 抛出，`str(exc)` 就是 i18n 键。同一时间只有一个设备任务（`window.job`）；目录、清单加载、更新检查各有自己的槽位。

esptool 一律以子进程运行（打包后是 `Flash32.exe --run-esptool …`），这样能随时 kill、逐行读输出。

## 4. 关键设计

### 4.1 识别板子（DetectJob）

1. `flash-id --after no-reset`：芯片、Flash 容量、MAC、Features（含片内 PSRAM）。
2. `read-flash 0x8000 0x8C00`：一次读出 0x8000 和 0x10000 两处可能的分区表。
3. 找到第一个 app 分区（factory 优先，否则最小的 OTA 槽），读它开头 0x100 字节，解析 `esp_app_desc_t`（项目名、版本、IDF 版本）。
4. `read-mac --after hard-reset` 把板子复位回应用。

中间步骤都用 `--after no-reset`：原生 USB（USB-Serial/JTAG）的板子每次硬复位都会掉线重新枚举，连续调用容易找不到串口。

**分区表位置以 0x10000 优先**：Muse 这类 bootloader 较大的固件把分区表放在 0x10000，刷它不会擦掉原厂固件留在 0x8000 的旧表；反过来，使用 0x8000 的固件，其 app 一般从 0x10000 开始，会覆盖那里的旧表。

PSRAM：esptool 只能看到封装在芯片里的 PSRAM（`Embedded PSRAM 8MB`）。ESP32-S3 片内 8 MB 及以上视为 Octal，其余视为 Quad；模组上外挂的 PSRAM 看不到，按"无/未知"处理。

### 4.2 插拔检测

`window._poll_ports` 每 1.5 秒列一次串口。新出现的、像 ESP32 的串口（见 `devices.KNOWN_USB`）会被选中，0.8 秒后自动识别。当前串口消失后要持续 5 秒才当作拔出，因为刷完硬复位时原生 USB 会短暂掉线。

### 4.3 固件目录（catalog.py + data/catalog.json）

来源（source）有两种 `kind`：

- `chip`：一种芯片一个固件（WLED、Tasmota、ESPHome、MicroPython），识别出芯片就能定下来。
- `board`：每款板子一个固件（Muse），芯片认不出板子，所以只缩小范围；仅剩一款，或这块板子（按 MAC）以前在本机刷过时才自动选中，否则显示「请选择你的板子…」。**不要为了方便去猜**：选错同芯片的板子，固件照样能刷进去，但跑在错误的硬件上。

选项（option）的来源，可以混用：

| 字段 | 例子 | 说明 |
|---|---|---|
| `options[].manifest_url` | ESPHome、立创 fork | 直接给清单网址 |
| `options[].template` + `version` | WLED、MicroPython | 内联清单模板，`{占位符}` 由 `version` 填：`{"github": "owner/repo"}` 给 `{tag}` `{version}`；`{"page": url, "regex": 命名分组}` 给分组值 |
| `boards_url` + `manifest_url` | Muse | Muse 网页刷机器的 boards.json，`{board}` 代入清单网址 |
| `index_url` + `index_key` | Tasmota | Tasmota 安装页的 manifests.json（带 chipFamilies） |

启动时 `CatalogJob` 并行列出所有来源（`listed` 信号先刷新界面），再并行读取各清单得知芯片支持；读不到的选项按"未知"保留，不当作不适用。

### 4.4 清单扩展：一个芯片多个 build

标准 ESP Web Tools 清单每个芯片只有一个 build。Flash32 允许多个，并在 build 里加：

```json
"flash32": {"flash": "16MB", "psram": "octal", "detect_flash_size": true}
```

`Manifest.build_for(chip, flash_bytes, psram)` 选择规则：排除需要比实际更大 Flash 的 build；优先 Flash 完全相等，其次 PSRAM 匹配（未检测到 PSRAM 时偏好无 PSRAM 要求的），再其次 Flash 最大的；并列时取清单中靠前的。`detect_flash_size` 会给 esptool 加 `--flash-size detect`，让它按实际容量改写 bootloader 头（WLED 只发布一个 8 MB 的 bootloader，网页版是在浏览器里改写的）。

WLED 模板照抄自 install.wled.me 的 `app.js`（CHIP_CONFIG / VARIANTS），包括它的怪癖：ESP32 4 MB 用的是 `partitions_c3_4m.bin`。WLED 改版时需要对照更新。

### 4.5 备份与还原（backups.py）

- 文件：`文档\Flash32\backups\<chip>-<mac>-<时间>.bin`，旁边 `.json` 记录 chip、flash_bytes、mac、created、board、firmware（从镜像里的分区表和 app 描述解析）。
- 「首刷前备份」：刷写任务识别后，如果这个 MAC 没有**完整**备份（文件大小 = Flash 容量），先读整片 Flash 再刷；备份失败就不刷。16 MB 在原生 USB 上约 3.7 分钟。
- 还原：从 0x0 写回，检查芯片一致、大小不超过 Flash；来自其他 MAC 的备份只提示不阻止。没有 `.json` 的 .bin（其他工具导出）也能用，芯片按当前识别结果。

### 4.6 自动更新（updater.py）

1. 启动 3 秒后（或点版本号）查 `api.github.com/repos/rw0104/Flash32/releases/latest`。
2. 版本号按数字比较（`parse_version`）。
3. 下载资产 `Flash32-win64.zip`，SHA-256 必须匹配 GitHub 的 asset `digest`（旧 Release 退回到说明里的 `SHA-256  Flash32-win64.zip  <hex>` 行）；都没有就拒绝。
4. 解压到 `%TEMP%\flash32-update-<版本>\new`，找到同时含 `Flash32.exe` 和 `_internal` 的目录。
5. 写 `install.ps1`（UTF-8 BOM）并以分离进程启动，程序退出。脚本：等待旧进程退出 → 备份当前 `_internal` 和 exe → `robocopy /MIR /IS /IT` 替换 `_internal`、复制 exe → 失败则还原 → 启动新版 → 成功后删除临时目录（失败时留下 `update.log`）。

只动 `Flash32.exe` 和 `_internal`：用户可能把 exe 解压在桌面，对整个目录 `/MIR` 会删光桌面。`/IS /IT` 不能省：robocopy 默认跳过大小和时间相同的文件。只有打包后的 Windows 版、且安装目录可写时才自动更新，否则引导到下载页。

## 5. 发布流程

1. 改 `flasher/__init__.py` 的 `__version__`（必须大于线上版本，否则旧版不会提示更新）。
2. `python -m unittest discover -s tests -t .`
3. 提交并推送 `main`。
4. `.\build.ps1`，然后把 `dist\Flash32` **文件夹**压缩成 `dist\Flash32-win64.zip`（压缩包里第一层是 `Flash32\`）。
5. 计算 SHA-256（`Get-FileHash dist\Flash32-win64.zip`）。
6. `gh release create vX.Y.Z dist/Flash32-win64.zip --title "Flash32 vX.Y.Z" --notes-file notes.md`。说明保持中英文，含「更新 / 下载 / 校验」三段和 SHA-256 行。
7. 确认 `gh api repos/rw0104/Flash32/releases/latest -q '.assets[].digest'` 与本地哈希一致。

**资产文件名 `Flash32-win64.zip` 和 Release 必须为 latest，是自动更新的约定，不要改。**

## 6. 踩过的坑

- **旧分区表残留**：见 4.1。最初优先读 0x8000，把 Muse 板识别成"没有固件"。
- **原生 USB 复位掉线**：识别中间步骤用 no-reset；插拔检测有 5 秒宽限。
- **robocopy 跳过"相同"文件**：见 4.6，是测试发现的。
- **PowerShell 路径**：脚本里所有路径都用单引号字符串（`'` 转义成 `''`），文件存 UTF-8 BOM，否则 Windows PowerShell 5 读错中文路径。测试覆盖了含空格、撇号和中文的路径。
- **在 Git Bash 里用 heredoc 写 Python 补丁**：`\n`、`\b`、`\0` 会被转义成真实字符（曾在源码里留下 NUL 和退格字节）。改文件时用编辑器或独立脚本文件。
- **Windows 控制台编码**：打印中文时设 `PYTHONIOENCODING=utf-8`，否则 GBK 报错。
- **build.ps1 输出**：在 PowerShell 里对它用 `2>&1` 会把 PyInstaller 的 stderr 当错误中断；用 `*> 日志文件`。

## 7. 已知限制与未验证项

未在真实硬件上验证（代码和单元测试已覆盖逻辑）：

- 刷入 WLED / Tasmota / ESPHome / MicroPython（尤其 WLED 的 `--flash-size detect`）。
- 在真实板子上「还原备份」。
- 打包版完整走一遍自动更新（需要下一个版本发布后，用 0.4.0 点「立即更新」）。

已验证：识别（读出 `muse-gadget 999.0.0`）、整片备份（16 MB，222 秒）、目录五个来源在 ESP32-S3 16 MB Octal PSRAM 上都解析到可下载文件、更新包的下载/校验/解压、替换脚本（含失败回滚）。

限制：

- 板子型号无法从芯片读出，只能靠芯片/Flash/USB ID/本机记录缩小范围。
- 外挂 PSRAM 检测不到。
- 下载源在 GitHub、github.io、raw.githubusercontent.com、micropython.org、install.wled.me，国内网络可能慢；GitHub API 未登录每小时 60 次。
- 仅打包 Windows；代码本身跨平台，但自更新只支持 Windows 打包版。exe 未签名，SmartScreen 会提示。
- 在识别过程中关闭程序，板子可能停在下载模式（重新插线即可）。

## 8. 以后可以做的

- 根据板子上现有固件的项目名（`esp_app_desc_t.project_name`，如 `muse-gadget`）默认选中同一项目，提示"升级"还是"换固件"。注意 Arduino 系固件的项目名常是 `arduino-lib-builder`，不可靠。
- 刷完后的 Wi-Fi 配置（Improv Serial，WLED/Tasmota/ESPHome 都支持）。
- 更多来源（如小智 AI 等按板子区分的项目）；在 `catalog.json` 加一项即可，见 4.3。
- macOS / Linux 打包、代码签名。
- 更新检查的开关、跳过某个版本。
