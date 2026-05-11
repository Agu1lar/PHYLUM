# PowerShell helper to install Python deps and Playwright browsers
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install --with-deps
Write-Host "Playwright installation complete."