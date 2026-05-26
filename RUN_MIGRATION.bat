@echo off
REM MIGRATION COMMAND FOR CLAUDE CODE (Windows)
REM Execute this to implement all Claude in Chrome migration changes

echo ==================================
echo Claude in Chrome Migration
echo ==================================
echo.
echo This will:
echo - Delete unnecessary MD files
echo - Create apply_agent.py
echo - Create apply_integration.py
echo - Create apply_logger.py
echo - Create config_loader.py
echo - Create apply_debugger.py
echo - Create comprehensive README.md
echo - Update main.py
echo - Setup resume loading from Dropbox
echo - Organize files to project root
echo.
echo ==================================
echo.
echo Copy the following command and run in your terminal:
echo.
echo claude-code.exe (or just: claude-code in PowerShell)
echo.
echo Then paste the complete prompt from PROMPT_FOR_CLAUDE_CODE.md
echo OR use this command:
echo.
echo PowerShell:
echo   Get-Content PROMPT_FOR_CLAUDE_CODE.md ^| claude-code
echo.
echo Command Prompt:
echo   type PROMPT_FOR_CLAUDE_CODE.md ^| claude-code
echo.
echo ==================================
echo.

REM Check if claude-code is available
where claude-code >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: claude-code command not found
    echo Please install Claude Code first
    pause
    exit /b 1
)

echo Starting Claude Code with migration prompt...
echo.

REM Use PowerShell to pipe the prompt to claude-code
powershell -Command "Get-Content 'PROMPT_FOR_CLAUDE_CODE.md' | claude-code"

echo.
echo ==================================
echo Migration Complete!
echo ==================================
echo.
echo Next steps:
echo 1. Review the changes in your project
echo 2. Test with: python apply_debugger.py --url "^<linkedin_job_url^>"
echo 3. Run full automation: python main.py
echo.
pause
