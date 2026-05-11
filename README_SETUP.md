Setup & CI instructions (resumo)

Este documento descreve passos para preparar o ambiente de desenvolvimento e CI para o projeto AgenteDesktop.

Requisitos locais
- Windows: PowerShell 7+ (pwsh) recomendado. Adicione pwsh ao PATH.
- Node.js v18+ (para frontend)
- Python 3.11+ (3.11 testado). Recomenda-se criar um virtualenv.

Passos rápidos (Windows)
1) Criar e ativar venv:
   python -m venv .venv
   .\.venv\Scripts\Activate
2) Instalar dependências Python:
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt -r requirements-dev.txt
3) Instalar Playwright (navegadores):
   python -m playwright install --with-deps
   -- ou use o helper: .\install_playwright.ps1
4) Rodar testes:
   python -m pytest -q

Passos rápidos (Linux / macOS)
1) python -m venv .venv
   source .venv/bin/activate
2) python -m pip install --upgrade pip
   python -m pip install -r requirements.txt -r requirements-dev.txt
3) python -m playwright install --with-deps
   -- ou use: ./install_playwright.sh
4) python -m pytest -q

PowerShell (Windows) — instalação rápida
- Baixar e instalar PowerShell 7 (pwsh): https://aka.ms/powershell
- Após instalar, confirme com:
  pwsh --version

Node.js (frontend)
- Instalar Node LTS: https://nodejs.org/
- No diretório frontend:
  cd frontend
  npm install
  npm run dev

CI (GitHub Actions)
- Foi adicionado .github/workflows/ci.yml com uma matrix (ubuntu/windows/macos).
- A job instala dependências e (quando configurado) instala browsers Playwright antes de rodar pytest.

Notas adicionais & troubleshooting
- Se o teste falhar por falta de pwsh no runner local, instale PowerShell 7 ou rode testes num runner Windows.
- Se tiver problemas com Playwright, rode: python -m playwright install --with-deps
- Para questões no frontend, cole a saída de npm run dev e eu ajudo.

Se quiser, eu posso:
- Ajustar CI para rodar apenas testes compatíveis por SO
- Adicionar badge de status no README
- Normalizar outras validações Pydantic automaticamente

Comandos úteis que você deve rodar agora (copiar/colar):
# (no Windows PowerShell com venv ativado)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install --with-deps
python -m pytest -q

# Frontend
cd frontend
npm install
npm run dev
