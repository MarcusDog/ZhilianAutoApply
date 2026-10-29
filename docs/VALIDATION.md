# 验证记录

验证日期：2026-10-30。环境：Windows x64、Python 3.14、已安装的 Google Chrome。

- 14 项本地自动化测试通过：配置合法性、关键词模式、岗位和详情过滤、薪资解析、收藏状态、持久化数量上限、去重、定时判断、暂停恢复、API 设置保存、跨站写入拒绝、日志编码容错和提交前拦截。
- 使用空白临时数据目录启动真正的便携 EXE，验证首次默认预览模式、不依赖原账号数据库。
- 启动器退出后，网页、JavaScript、样式和 Playwright 驱动仍存在并可运行。
- 再次启动同一 EXE 复用已有服务。
- 打包后的 Playwright 驱动启动 Chrome、打开本地测试页面并成功读取标题。
- 实际工作进程打开新的登录窗口，完成暂停、复用同一进程恢复、关闭任务的验证。全过程为预览模式，不提交简历。
- Web 界面实际保存配置并刷新，确认设置保留；390 像素宽度下无水平溢出；检查期间没有 JavaScript 页面错误。

以上是在一台 Windows 电脑上通过独立数据目录模拟新安装的验证，不等同于所有品牌电脑和系统版本均已实测。便携包需要完整解压，保留 `_internal/`，并安装 Chrome/Edge，或准备 Playwright Chromium。登录、验证码、平台限额和平台页面改版仍可能影响实际运行。

可重复执行：

```powershell
uv run python -m unittest discover -s tests -t . -v
uv run python scripts/check_web.py
uv run python scripts/smoke_windows.py dist/ZhilianApplyDesk/ZhilianApplyDesk.exe --browser-worker --output build/smoke-result.json
```
