# Flash32 · ESP32 刷机助手

[English](README.en.md)

给不熟悉命令行的人用的 ESP32 刷机工具：插上板子，程序认出芯片，列出能刷的开源固件，点一下就刷好。不需要安装 Python、ESP-IDF 或任何开发环境。

## 能做什么

- **以板子为中心**：插上 USB 就自动选中串口，识别芯片、Flash 容量、片内 PSRAM、MAC，以及板子上现在跑的固件。固件目录只列出适合这块板子的项目，并自动选对版本（例如 16 MB + Octal PSRAM 的 ESP32-S3 会拿到对应的那一版）。
- **内置固件目录**，在线读取各项目的最新版本：
  - [WLED](https://kno.wled.ge)：LED 灯带控制
  - [Tasmota](https://tasmota.github.io/docs/)：智能插座、开关、传感器（含显示屏、红外、蓝牙等变体）
  - [ESPHome](https://esphome.io)：Home Assistant 设备的入门固件
  - [MicroPython](https://micropython.org)：在板子上写 Python
  - [Muse Gadget](https://github.com/facebookincubator/muse-gadget-sdk)：语音助手，按板子区分（9 款官方板子 + 立创·实战派 ESP32-S3 社区移植版）
- **按芯片 vs 按板子**：WLED、Tasmota、ESPHome、MicroPython 一种芯片一个固件，识别出芯片就够了；Muse 这类带屏幕、音频的固件每款板子不同，而芯片本身认不出自己焊在哪块板子上，所以程序会把范围缩小到适用的几款，由你确认型号（只剩一款，或这块板子以前在这里刷过时会自动选中）。
- **任意开源固件**：粘贴一个 ESP Web Tools 固件清单网址（`manifest.json`）就能刷。
- **本地文件**：选择 `manifest.json`、固件文件夹，或单个 `.bin` 文件（指定芯片和写入地址）。
- **备份与还原原固件**：每块板子第一次刷机前，自动把整片 Flash 备份到「文档\Flash32\backups」（可关闭，也可以随时手动备份）；用「还原备份」随时刷回出厂固件。
- **刷前检查**：芯片或 Flash 容量不匹配的固件直接拒绝刷写。
- **设备设置**：固件需要时（例如 Muse 的 SDK token），刷完后直接通过串口写入。日志里会自动隐藏密钥。
- **串口日志**：查看设备运行输出，方便排查问题。
- 中文 / English 界面，跟随系统语言，也可以手动切换。

## 使用方法

1. 下载 `Flash32-win64.zip`，解压后双击 `Flash32.exe`。`_internal` 文件夹要和 exe 放在一起。
2. **连接设备**：用**能传数据**的 USB 线连接板子，程序会自动选中串口并识别；没反应时点「刷新」或「识别设备」。
3. **选择固件**：目录里只剩适合这块板子的项目，选一个即可；按板子区分的固件还要选出你的板子型号。也可以切换到「网址」或「本地文件」。
4. 点 **开始刷机**，等进度条走完，刷写过程中不要拔线。第一次刷这块板子时会先备份原固件。
5. 按固件说明完成设置：比如 WLED、Tasmota 刷完后会开一个 Wi-Fi 热点；Muse 需要在「设置设备」里写入 SDK token。

### 常见问题

- **找不到串口**：换一根能传数据的线（有些线只能充电）。CH340 芯片的板子可能需要先装[驱动](https://www.wch.cn/downloads/CH341SER_EXE.html)。
- **连不上下载模式**：按住板子上的 BOOT 键，按一下 RESET（或重新插线），松开 BOOT 后再试；也可以把刷写速度改成 115200。
- **串口被占用**：关掉其他串口工具，包括本程序的「串口日志」。
- **全新安装**：换成另一种固件时建议勾选，会清空设备上的旧数据；同一个固件升级不用勾选，Wi-Fi 和配对信息都会保留。

## 给固件作者：让你的固件出现在目录里

用 ESP-IDF 编译后，把编译目录导出成固件文件夹：

```powershell
python tools/export_manifest.py <build 目录> <输出目录> "固件名称" 版本号
```

输出目录里是各个镜像文件和 `manifest.json`，格式和 ESP Web Tools 兼容。把它放到 GitHub Pages 或 Release 上，用户就能通过网址刷写。

想收录进内置目录，就往 `data/catalog.json` 里添加一项，然后提交 PR。`kind` 写 `chip`（一种芯片一个固件）或 `board`（每款板子一个固件）。选项可以用以下任意方式提供：

- `options`：直接列出，每项给一个 `manifest_url`，或者一个带 `{占位符}` 的清单 `template`，配合 `version` 自动取最新版本（`{"github": "owner/repo"}`，或 `{"page": 网址, "regex": 带命名分组的正则}`）；
- `boards_url`：像 Muse 那样指向一个 `boards.json`；
- `index_url` + `index_key`：像 Tasmota 那样的清单索引。

同一种芯片可以给多个 build，在 build 里加 `"flash32": {"flash": "8MB", "psram": "octal"}` 标明适用的 Flash 容量和 PSRAM，程序会按识别结果选择。`"detect_flash_size": true` 会让 esptool 按实际容量修正 bootloader。

镜像按各自的地址分别写入（和 `idf.py flash` 一样），不会合并成一个大文件，所以升级时设备上保存的设置会保留。

## 从源码运行 / 打包

代码结构、设计说明和发布流程见 [开发文档](docs/DEVELOPMENT.md)。

需要 Python 3.12+，Windows、macOS、Linux 都可以运行：

```bash
python -m pip install -r requirements.txt
python app.py
python -m unittest discover -s tests -t .
```

打包 Windows 版本（会自动创建独立的构建环境）：

```powershell
.\build.ps1          # 产出 dist\Flash32\Flash32.exe
```

## 致谢

- [esptool](https://github.com/espressif/esptool)（乐鑫）负责实际的刷写。
- 固件清单格式来自 [ESP Web Tools](https://esphome.github.io/esp-web-tools/)。
- 目录里的固件分别属于 WLED、Tasmota、ESPHome、MicroPython、Muse Gadget SDK 各项目，遵循各自的许可证；本工具只负责下载和刷写。

本工具与乐鑫及上述项目没有隶属关系。许可证：Apache-2.0。
