# Flash32 · ESP32 刷机助手

[English](README.en.md)

给不熟悉命令行的人用的 ESP32 刷机工具：选固件、插上 USB、点一下就刷好。不需要安装 Python、ESP-IDF 或任何开发环境。

## 能做什么

- **固件目录**：内置开源项目的在线固件目录，选择你的板子就能下载刷写。目前收录 [Muse Gadget](https://github.com/facebookincubator/muse-gadget-sdk)（Meta 开源语音助手，9 款官方板子）。
- **任意开源固件**：粘贴一个 ESP Web Tools 固件清单网址（`manifest.json`）就能刷。ESPHome、WLED、Tasmota 等项目网页上的「Install」按钮用的都是这种清单。
- **本地文件**：选择 `manifest.json`、固件文件夹，或单个 `.bin` 文件（指定芯片和写入地址）。
- **自动识别**：识别芯片型号和 Flash 容量，固件不匹配时直接拒绝刷写，避免刷错。
- **设备设置**：刷 Muse 固件后，可以直接写入 SDK token（以及可选的千问语音播报 key）。日志里会自动隐藏这些密钥。
- **串口日志**：查看设备运行输出，方便排查问题。
- 中文 / English 界面，跟随系统语言，也可以手动切换。

## 使用方法

1. 下载 `Flash32-win64.zip`，解压后双击 `Flash32.exe`。`_internal` 文件夹要和 exe 放在一起。
2. **选择固件**：在「固件目录」里选择项目和你的板子，也可以切换到「网址」或「本地文件」。
3. **连接设备**：用**能传数据**的 USB 线连接板子，点「刷新」，选择串口。可以点「识别设备」确认芯片。
4. 点 **开始刷机**，等进度条走完，刷写过程中不要拔线。
5. 刷 Muse 固件的话：在「设置设备」里填写 SDK token，点「写入设备」，然后在手机 Muse App 里添加设备。

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

输出目录里是各个镜像文件和 `manifest.json`，格式和 ESP Web Tools 兼容。把它放到 GitHub Pages 或 Release 上，用户就能通过网址刷写。如果想收录进内置目录，往 `data/catalog.json` 里添加一项，然后提交 PR：可以直接列出 `boards`，也可以像 Muse 那样指向一个 `boards.json`。

镜像按各自的地址分别写入（和 `idf.py flash` 一样），不会合并成一个大文件，所以升级时设备上保存的设置会保留。

## 从源码运行 / 打包

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
- [Muse Gadget SDK](https://github.com/facebookincubator/muse-gadget-sdk)（Meta，Apache-2.0）。

本工具与乐鑫、Meta 没有隶属关系。许可证：Apache-2.0。
