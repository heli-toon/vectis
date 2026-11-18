# Vectis Production Deployment & Turnkey Operations Guide

This guide covers production deployment strategies for Vectis, enabling zero-downtime, memory-efficient (< 35 MB idle, < 65 MB peak) execution across Linux bare-metal, VPS instances, and containerized cloud environments.

---

## 1. Quick Installation & Packaging (PEP 621)

Vectis conforms to PEP 621 / PEP 517 packaging standards via `pyproject.toml`.

### Standard / Editable Installation
```bash
# Clone the repository
git clone https://github.com/muhaimin/vectis.git
cd vectis

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in editable development mode or standard mode
pip install -e .

# The direct 'vectis' CLI entrypoint is now globally available in your environment:
vectis --help
vectis status
```

### Python Module Invocation
Alternatively, Vectis can be executed as a module:
```bash
python -m vectis run
python -m vectis ui --port 8080
```

---

## 2. Docker & Container Deployment

Vectis provides an ultra-minimal, multi-stage Alpine Linux image targeting **< 65 MB total footprint**.

### Dockerfile Architecture
* **Builder Stage:** Uses `python:3.12-alpine` to compile and vendor dependencies.
* **Runner Stage:** Minimal runtime containing only Python and installed packages. No compilers, build artifacts, or test suites are included in the final image.
* **Non-Root Execution:** Runs under dedicated UID/GID `1000:1000` (`vectis`).
* **Auto-Initialization:** `docker-entrypoint.sh` automatically detects if the database is missing and seeds default agents and schema upon initial boot.

### Building & Running Manually
```bash
# Build the Alpine image
docker build -t vectis:latest .

# Run the background daemon with persistent volume mounts
docker run -d \
  --name vectis-daemon \
  --restart unless-stopped \
  -v vectis-data:/app/data \
  -v vectis-workspaces:/app/workspaces \
  -e GEMINI_API_KEY="your-key-here" \
  -e GROQ_API_KEY="your-key-here" \
  vectis:latest run

# Inspect live status
docker exec -it vectis-daemon vectis status

# Seed a task into the running container
docker exec -it vectis-daemon vectis seed "Deploy v1 release" --assign ceo
```

---

## 3. Docker Compose (Turnkey Multi-Service Stack)

The bundled `docker-compose.yml` launches both the **heartbeat daemon** and the **zero-dependency web UI dashboard** simultaneously, sharing database state over SQLite WAL mode without locking.

### Setup
1. Copy environment template:
   ```bash
   cp .env.example .env
   # Add your API keys (GEMINI_API_KEY, GROQ_API_KEY, etc.)
   ```

2. Start the services:
   ```bash
   docker compose up -d
   ```

3. Access the services:
   * **Web Dashboard:** Open [http://localhost:8080](http://localhost:8080)
   * **Daemon Logs:** `docker compose logs -f daemon`
   * **Stop Services:** `docker compose down`

---

## 4. Linux VPS / Bare-Metal Deployment (Systemd)

For deploying directly to a $5/month cloud VPS (e.g. Debian, Ubuntu, Rocky Linux):

### 1. Provision Application Directory
```bash
sudo useradd -r -s /bin/false vectis || true
sudo mkdir -p /opt/vectis /etc/vectis
sudo chown -R vectis:vectis /opt/vectis

# Copy Vectis source to /opt/vectis
cd /opt/vectis
sudo -u vectis python3 -m venv .venv
sudo -u vectis /opt/vectis/.venv/bin/pip install -e .
```

### 2. Configure Environment Secrets
Create `/etc/vectis/vectis.env` (accessible only by root/vectis):
```bash
sudo tee /etc/vectis/vectis.env > /dev/null <<EOF
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key
ANTHROPIC_API_KEY=your_anthropic_key
OPENAI_API_KEY=your_openai_key
VECTIS_DB_PATH=/opt/vectis/data/vectis.db
VECTIS_WORKSPACES_DIR=/opt/vectis/workspaces
VECTIS_POLL_INTERVAL=30
VECTIS_MAX_WORKERS=4
EOF

sudo chmod 600 /etc/vectis/vectis.env
sudo chown vectis:vectis /etc/vectis/vectis.env
```

### 3. Initialize Database
```bash
sudo -u vectis /opt/vectis/.venv/bin/vectis init
```

### 4. Install Systemd Services
```bash
# Copy unit files
sudo cp vectis.service /etc/systemd/system/
sudo cp vectis-ui.service /etc/systemd/system/

# Reload systemd and enable services on boot
sudo systemctl daemon-reload
sudo systemctl enable --now vectis.service
sudo systemctl enable --now vectis-ui.service
```

### 5. Service Maintenance & Monitoring
```bash
# Check daemon status
sudo systemctl status vectis.service

# Stream logs in real-time
sudo journalctl -u vectis.service -f

# Check web UI status
sudo systemctl status vectis-ui.service
```

---

## 5. Security & Resource Optimization

* **Memory Budget Guardrail:** Bounded worker threads (`VECTIS_MAX_WORKERS=4`) limit peak process RAM to < 65 MB.
* **Workspace Isolation:** Subprocess execution and file I/O are confined strictly to `workspaces/ticket_<id>/` with path traversal protections.
* **Systemd Hardening:** `ProtectSystem=full`, `ProtectHome=read-only`, and `NoNewPrivileges=true` prevent escape from the service working directory.
