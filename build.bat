@echo off
chcp 65001 >nul
cd /d %~dp0

echo ============================================
echo   网络一键修复工具 - 打包脚本
echo ============================================

REM 没有 pyinstaller 就自动安装
pyinstaller --version >nul 2>&1 || (
    echo [提示] 未检测到 PyInstaller，正在安装...
    python -m pip install pyinstaller || goto :fail
)

REM --uac-admin : 双击 exe 时自动弹出 UAC 请求管理员权限
REM --windowed  : 运行时不显示黑色控制台窗口
pyinstaller --noconfirm --clean --onefile --windowed --uac-admin ^
    --name NetworkFix wifi_fixer.py || goto :fail

echo.
echo 打包完成: dist\NetworkFix.exe
echo 可将该 exe 复制到任意位置（或发送快捷方式到桌面）直接双击使用。
pause
exit /b 0

:fail
echo.
echo 打包失败，请检查上方错误信息。
pause
exit /b 1
