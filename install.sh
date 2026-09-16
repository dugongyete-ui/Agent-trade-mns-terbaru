#!/usr/bin/env bash
set -e

echo "========================================"
echo "  AI Dzeck — Install Script"
echo "========================================"

# ── 0. Prerequisites check ────────────────────────────────────────────────────
echo ""
echo "[0/5] Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install Python 3.12 or later." && exit 1
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
  echo "ERROR: Python 3.12+ required, found $PY_VER" && exit 1
fi
echo "      Python $PY_VER OK"

if ! command -v node &>/dev/null; then
  echo "ERROR: node not found. Install Node.js 18 or later." && exit 1
fi
NODE_VER=$(node --version)
echo "      Node.js $NODE_VER OK"

if ! command -v pnpm &>/dev/null; then
  echo "      pnpm not found — installing via npm..."
  npm install -g pnpm
fi
PNPM_VER=$(pnpm --version)
echo "      pnpm $PNPM_VER OK"

# ── Detect pip install flags ──────────────────────────────────────────────────
PIP_FLAGS=""
if python3 -m pip install --break-system-packages --dry-run pip &>/dev/null 2>&1; then
  PIP_FLAGS="--break-system-packages"
fi

# Upgrade pip/setuptools first to avoid resolver issues
python3 -m pip install $PIP_FLAGS -q --upgrade pip setuptools wheel 2>/dev/null || true

# ── 1. Frontend dependencies ──────────────────────────────────────────────────
echo ""
echo "[1/5] Installing frontend dependencies..."
cd frontend
pnpm approve-builds --yes 2>/dev/null || true
pnpm install --frozen-lockfile 2>/dev/null || pnpm install
cd ..
echo "      Frontend dependencies installed"

# ── 2. Core backend dependencies ─────────────────────────────────────────────
echo ""
echo "[2/5] Installing core backend dependencies..."

python3 -m pip install $PIP_FLAGS -q \
  "async-lru>=2.0.0" \
  "beanie>=1.25.0" \
  "cryptography>=3.4.8" \
  "email-validator>=2.3.0" \
  "fastapi>=0.121.2" \
  "httpx>=0.28.1" \
  "pydantic>=2.12.4" \
  "pydantic-settings>=2.12.0" \
  "pyjwt[crypto]>=2.8.0" \
  "pymongo>=4.14.0" \
  "python-dotenv>=1.2.1" \
  "python-multipart>=0.0.20" \
  "redis>=5.0.1" \
  "rich>=14.2.0" \
  "sse-starlette>=3.0.3" \
  "uvicorn>=0.38.0" \
  "websockets>=15.0.1"

echo "      Core dependencies installed"

# ── 3. AI / LLM dependencies ─────────────────────────────────────────────────
echo ""
echo "[3/5] Installing AI/LLM dependencies..."

# 3a. OpenAI client
python3 -m pip install $PIP_FLAGS -q \
  "openai>=2.8.0"

# 3b. LangChain ecosystem + all provider adapters
python3 -m pip install $PIP_FLAGS -q \
  "langchain>=1.0.7" \
  "langchain-classic>=1.0.7" \
  "langchain-openai>=1.0.3" \
  "langchain-anthropic>=1.2.0" \
  "langchain-deepseek>=1.0.1" \
  "langchain-ollama>=1.0.0"

# 3c. Search
python3 -m pip install $PIP_FLAGS -q \
  "tavily-python>=0.5.0"

echo "      AI/LLM dependencies installed"

# ── 4. Utility + MCP dependencies ────────────────────────────────────────────
echo ""
echo "[4/5] Installing utility dependencies..."

# MCP SDK version is LOCKED to the exact same pin as backend/pyproject.toml.
# The local MCP servers use mcp-servers/_legacy_mcp_compat.py, which supports
# the 2.x constructor-kwarg API and the 1.27 decorator API — but mixing a
# different major between installs is what previously caused 4 MCP servers
# to crash at startup ("Unknown tool" errors in the UI). Never widen this pin.
python3 -m pip install $PIP_FLAGS -q \
  "mcp==2.0.0"

# ── 4a. MCP server dependencies ───────────────────────────────────────────────
echo "      Installing MCP server dependencies..."
python3 -m pip install $PIP_FLAGS -q \
  "tradingview-mcp>=0.9.1" \
  "tradingview-screener>=0.1.0" 2>/dev/null || \
  echo "      WARNING: tradingview-mcp install failed — TradingView MCP unavailable"

# ── 4b. File extraction dependencies ─────────────────────────────────────────
echo "      Installing file-extraction dependencies..."
python3 -m pip install $PIP_FLAGS -q \
  "python-docx>=1.2.0" \
  "python-pptx>=1.0.0" \
  "pdfplumber>=0.11.0" \
  "pandas>=2.0.0" \
  "openpyxl>=3.1.0"

echo "      Utility dependencies installed"

# ── 4c. Dev / test dependencies ───────────────────────────────────────────────
echo "      Installing dev/test dependencies..."
python3 -m pip install $PIP_FLAGS -q \
  "pytest>=7.0.0" \
  "pytest-asyncio>=0.21.0" \
  "pytest-cov>=4.0.0" \
  "pytest-mock>=3.10.0" \
  "requests>=2.28.0"

# ── 5. Environment configuration ─────────────────────────────────────────────
echo ""
echo "[5/5] Checking environment configuration..."

ENV_SRC=""
if [ -f .env.example ]; then
  ENV_SRC=".env.example"
elif [ -f backend/.env.example ]; then
  ENV_SRC="backend/.env.example"
fi

if [ ! -f backend/.env ]; then
  if [ -n "$ENV_SRC" ]; then
    cp "$ENV_SRC" backend/.env
    echo "      Created backend/.env from $ENV_SRC"
    echo "      Edit backend/.env and fill in your API keys before starting"
  else
    echo "      WARNING: No .env.example found — create backend/.env with your config"
    echo "      Required: API_KEY, API_BASE, MODEL_NAME, MODEL_PROVIDER"
    echo "      Required: MONGODB_URI, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD"
    echo "      Required: JWT_SECRET_KEY, PASSWORD_SALT, AUTH_PROVIDER"
    echo "      Optional: TAVILY_API_KEY, TV_PROXY_BASE, VISION_MODEL_NAME"
  fi
else
  echo "      backend/.env already exists"
fi

# ── 6. Verification gate (anti-mismatch) ─────────────────────────────────────
echo ""
echo "[6/6] Verifying installation..."

python3 - <<'PYVERIFY'
import importlib, importlib.metadata as im, os, sys

# 1) Critical modules must import (catches venv resets / partial installs)
critical = [
    "fastapi", "pymongo", "beanie", "redis", "httpx", "uvicorn",
    "openai", "langchain", "langchain_classic", "langchain_openai",
    "tavily", "mcp", "tradingview_screener", "pandas", "openpyxl",
    "docx", "pptx", "pdfplumber",
]
missing = []
for mod in critical:
    try:
        importlib.import_module(mod)
    except Exception as exc:  # noqa: BLE001
        missing.append(f"{mod} ({type(exc).__name__})")

if missing:
    print("VERIFICATION FAILED - missing/broken modules:")
    for m in missing:
        print(f"  - {m}")
    print("Re-run install.sh or install from backend/pyproject.toml.")
    sys.exit(1)

# 2) mcp SDK must be 2.x (the generation the stack is tested against;
#    keep this in sync with the "mcp==..." pin in step [4/5] above)
try:
    mcp_version = im.version("mcp") or "unknown"
except Exception:  # noqa: BLE001
    mcp_version = "unknown"
if not str(mcp_version).startswith("2."):
    print(f"VERIFICATION FAILED - mcp SDK is {mcp_version!r}, expected 2.0.0.")
    print('Run: python3 -m pip install "mcp==2.0.0"')
    sys.exit(1)

# 3) The MCP-server shim must support this SDK generation
for cand in ("mcp-servers/_legacy_mcp_compat.py", "../mcp-servers/_legacy_mcp_compat.py"):
    if os.path.exists(cand):
        shim = cand
        break
else:
    shim = None
if shim:
    import importlib.util
    spec = importlib.util.spec_from_file_location("_shim_check", shim)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        srv = mod.make_server(
            "verify",
            on_list_tools=lambda c, p: None,
            on_call_tool=lambda c, p: None,
        )
        assert srv is not None
    except Exception as exc:  # noqa: BLE001
        print(f"VERIFICATION FAILED - MCP shim incompatible with mcp {mcp_version}: {exc}")
        sys.exit(1)

print(f"      mcp {mcp_version} OK - all {len(critical)} critical modules import OK")
PYVERIFY

echo ""
echo "========================================"
echo "  Installation complete & verified!"
echo ""
echo "  Start the backend:"
echo "    cd backend && python3 -m uvicorn app.main:app --host localhost --port 8000"
echo ""
echo "  Start the frontend:"
echo "    cd frontend && pnpm dev"
echo "========================================"
