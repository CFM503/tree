@echo off
echo ==========================================
echo   掌通家园监控 - Windows打包
echo ==========================================

echo.
echo [1/3] 清理旧的构建文件...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo.
echo [2/3] PyInstaller打包...
python -m PyInstaller build.spec --noconfirm

echo.
echo [3/3] 复制mpv到打包目录...
if not exist "dist\掌通家园监控\mpg" mkdir "dist\掌通家园监控\mpg"
copy /Y "mpg\mpv.exe" "dist\掌通家园监控\mpg\mpv.exe" >nul 2>&1

echo.
echo ==========================================
echo   打包完成！
echo   输出目录: dist\掌通家园监控\
echo   运行: dist\掌通家园监控\掌通家园监控.exe
echo ==========================================
echo.
echo 用户使用时需要:
echo   1. 解压整个 dist\掌通家园监控\ 文件夹
echo   2. 双击 掌通家园监控.exe 运行
echo.
pause
