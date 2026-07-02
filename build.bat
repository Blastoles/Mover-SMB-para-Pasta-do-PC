@echo off
echo Instalando dependencias...
pip install -r requirements.txt pyinstaller
echo.
echo Executando PyInstaller...
python -m PyInstaller --clean PrinterScanMover.spec
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERRO: Build falhou!
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo Sucesso: Executavel gerado na pasta dist/
pause

