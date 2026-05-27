@echo off
echo ==========================================
echo   猴子看护 - Windows单文件打包
echo ==========================================

echo.
echo [1/2] 清理旧的构建文件...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo.
echo [2/2] PyInstaller打包成单文件...
python -m PyInstaller build.spec --noconfirm

echo.
echo ==========================================
echo   打包完成！
echo   输出文件: dist\猴子看护.exe
echo ==========================================
echo.
echo 用户使用时需要:
echo   双击 dist\猴子看护.exe 即可直接运行（已内置播放器与组件，即开即用）
echo.
pause
