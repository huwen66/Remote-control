# Tasks

- [x] Task 1: 环境准备与依赖确认
  - [x] SubTask 1.1: 确认 Python 3 可用，安装所需第三方库（pillow、pyautogui、pynput、numpy）
  - [x] SubTask 1.2: 创建项目目录结构 `remote_control/`，包含 `server.py`、`client.py`、`common.py`

- [x] Task 2: 实现公共模块 common.py
  - [x] SubTask 2.1: 定义通信协议常量（消息类型：AUTH、FRAME、EVENT）
  - [x] SubTask 2.2: 实现 send_msg / recv_msg 辅助函数（4字节长度头 + payload）
  - [x] SubTask 2.3: 定义鼠标/键盘事件的序列化与反序列化方法（JSON）

- [x] Task 3: 实现服务端 server.py
  - [x] SubTask 3.1: 实现 TCP 服务器监听，支持单客户端连接
  - [x] SubTask 3.2: 实现密码认证握手逻辑
  - [x] SubTask 3.3: 实现屏幕捕获线程（pillow ImageGrab），JPEG 压缩后发送帧数据
  - [x] SubTask 3.4: 实现事件接收线程，解析鼠标/键盘事件并用 pyautogui 执行
  - [x] SubTask 3.5: 实现服务端 tkinter GUI（显示 IP、端口、密码、连接状态）

- [x] Task 4: 实现客户端 client.py
  - [x] SubTask 4.1: 实现连接界面（输入 IP、端口、密码，点击连接按钮）
  - [x] SubTask 4.2: 实现 TCP 连接与密码认证握手
  - [x] SubTask 4.3: 实现帧接收线程，解码 JPEG 并用 tkinter Canvas 渲染
  - [x] SubTask 4.4: 实现鼠标事件捕获（移动、点击、滚轮），坐标映射后发送
  - [x] SubTask 4.5: 实现键盘事件捕获并发送

- [x] Task 5: 集成测试与演示验证
  - [x] SubTask 5.1: 在同一台机器上分别启动 server.py 和 client.py，验证连接与画面显示
  - [x] SubTask 5.2: 验证鼠标点击、键盘输入在服务端正确执行
  - [x] SubTask 5.3: 验证错误密码时连接被拒绝

# Task Dependencies
- Task 2 依赖 Task 1
- Task 3 依赖 Task 2
- Task 4 依赖 Task 2
- Task 5 依赖 Task 3 和 Task 4
