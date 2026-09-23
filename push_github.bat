@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title iRich push GitHub
echo ========================================
echo  Push both repos
echo    1) PUNteerased/iRich
echo    2) PUNteerased/iRich_Dashboard
echo ========================================
echo.

REM Commit message: pass as args, or prompt
set "MSG=%*"
if "%MSG%"=="" (
  set /p "MSG=Commit message: "
)
if "%MSG%"=="" set "MSG=Update iRich"

echo Message: %MSG%
echo.

REM ---------- 1) Monorepo (engine + dashboard copy) ----------
echo [1/2] iRich monorepo ...
git add -A
git status --short
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "%MSG%"
  if errorlevel 1 (
    echo ERROR: monorepo commit failed
    goto :fail
  )
) else (
  echo No monorepo changes to commit.
)
git push origin main
if errorlevel 1 (
  echo ERROR: monorepo push failed
  goto :fail
)
echo Monorepo OK.
echo.

REM ---------- 2) Dashboard-only (Vercel) ----------
echo [2/2] iRich_Dashboard ...
pushd "%~dp0dashboard" || goto :fail
git add -A
git status --short
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "%MSG%"
  if errorlevel 1 (
    echo ERROR: dashboard commit failed
    popd
    goto :fail
  )
) else (
  echo No dashboard changes to commit.
)
git push origin main
if errorlevel 1 (
  echo ERROR: dashboard push failed
  popd
  goto :fail
)
popd
echo Dashboard OK.
echo.

echo ========================================
echo  Done.
echo  https://github.com/PUNteerased/iRich
echo  https://github.com/PUNteerased/iRich_Dashboard
echo ========================================
echo.
pause
endlocal
exit /b 0

:fail
echo.
echo Push aborted — fix the error above and retry.
pause
endlocal
exit /b 1
