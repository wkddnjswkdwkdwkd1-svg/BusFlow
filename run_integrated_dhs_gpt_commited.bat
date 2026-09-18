@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py app_integrated_dhs_gpt_commited.py --open-browser
goto done
:use_python
python app_integrated_dhs_gpt_commited.py --open-browser
:done
echo.
echo Time Keeper stopped. Review any error above.
pause
