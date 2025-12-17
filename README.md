# BeachVar Device

Software para dispositivo de captura de video (Raspberry Pi) do sistema BeachVar.

## Funcionalidades

- Streaming de cameras RTSP via FFmpeg
- HLS output para Cloudflare Stream
- Terminal SSH via navegador (ttyd)
- Monitoramento de sistema (CPU, memoria, temperatura)
- Auto-restart de streams com retry progressivo

## Requisitos

- Raspberry Pi 4 ou superior (ARM64)
- Docker e Docker Compose
- Camera IP com suporte RTSP

## Instalacao

### 1. Clonar o repositorio

```bash
git clone https://github.com/beachvar/beachvar-device.git
cd beachvar-device
```

### 2. Configurar usuario SSH (para terminal web)

O terminal web permite acesso SSH ao host via navegador. Para funcionar sem senha, crie um usuario dedicado:

```bash
# Criar usuario 'device' com permissao sudo
sudo useradd -m -s /bin/bash -G sudo device

# Permitir sudo sem senha
echo "device ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/device
sudo chmod 440 /etc/sudoers.d/device

# Gerar chave SSH no diretorio do usuario
sudo -u device ssh-keygen -t ed25519 -f /home/device/.ssh/id_ed25519 -N ""

# Configurar authorized_keys para login sem senha
sudo -u device bash -c 'cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys'
sudo chmod 600 /home/device/.ssh/authorized_keys

# Copiar chave para local acessivel pelo Docker
sudo mkdir -p /etc/beachvar
sudo cp /home/device/.ssh/id_ed25519 /etc/beachvar/ssh_key
sudo chmod 644 /etc/beachvar/ssh_key
```

### 3. Configurar variaveis de ambiente

Crie o arquivo `.env`:

```bash
# Device identification
DEVICE_ID=device-001
DEVICE_TOKEN=seu-token-aqui

# Backend API
BACKEND_URL=https://api.beachvar.com

# Cloudflare (opcional, para gravacao)
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
```

### 4. Iniciar o container

```bash
docker compose up -d
```

## Configuracao

### Docker Compose

O arquivo `docker-compose.yml` configura:

```yaml
services:
  device:
    build: .
    command: python -O main.py
    env_file:
      - .env
    environment:
      # SSH config for web terminal (access to host)
      - SSH_HOST=localhost
      - SSH_USER=device
      - SSH_PORT=22
      - SSH_KEY_PATH=/ssh/id_ed25519
    volumes:
      # Mount SSH key for passwordless authentication to host
      - /etc/beachvar/ssh_key:/ssh/id_ed25519:ro
    ports:
      - "8080:8080"   # Main HTTP server
      - "7682:7682"   # ttyd web terminal
    restart: unless-stopped
    network_mode: host
```

### Variaveis de Ambiente

| Variavel | Descricao | Default |
|----------|-----------|---------|
| `DEVICE_ID` | Identificador unico do device | `unknown` |
| `DEVICE_TOKEN` | Token de autenticacao com backend | - |
| `BACKEND_URL` | URL da API backend | - |
| `SSH_HOST` | Host para conexao SSH | `localhost` |
| `SSH_USER` | Usuario SSH | `pi` |
| `SSH_PORT` | Porta SSH | `22` |
| `SSH_KEY_PATH` | Caminho da chave SSH no container | - |

## Endpoints

### Publicos

| Endpoint | Descricao |
|----------|-----------|
| `GET /health` | Health check |
| `GET /hls/{camera_id}/{filename}` | Arquivos HLS (m3u8, ts) |

### Admin (protegidos via Cloudflare)

| Endpoint | Descricao |
|----------|-----------|
| `GET /admin/` | Interface web |
| `GET /admin/status` | Status do device |
| `GET /admin/system` | Informacoes do sistema |
| `POST /admin/restart` | Reiniciar device |
| `GET /admin/terminal-config` | Config do terminal SSH |
| `GET /admin/registered-cameras` | Listar cameras |
| `POST /admin/registered-cameras` | Criar camera |
| `DELETE /admin/registered-cameras/{id}` | Remover camera |
| `GET /admin/streams` | Listar streams ativos |
| `POST /admin/streams/{id}/start` | Iniciar stream |
| `POST /admin/streams/{id}/stop` | Parar stream |

## Desenvolvimento

### Build local

```bash
docker build -t beachvar-device .
```

### Executar sem Docker

```bash
# Criar ambiente virtual
python -m venv .venv
source .venv/bin/activate

# Instalar dependencias
pip install -e .

# Executar
python main.py
```

## CI/CD

O GitHub Actions automaticamente:

1. Build da imagem Docker para ARM64
2. Push para GitHub Container Registry (ghcr.io)
3. Tags: `latest`, `main`, `v*.*.*`, commit SHA

### Usar imagem do registry

```bash
docker pull ghcr.io/beachvar/beachvar-device:latest
```

## Arquitetura

```
beachvar-device/
├── src/
│   ├── http/           # Servidor HTTP (aiohttp)
│   │   ├── server.py   # Routes e handlers
│   │   └── static/     # Frontend web
│   ├── streaming/      # Gerenciamento de streams
│   │   └── manager.py  # FFmpeg, retry logic
│   └── ...
├── main.py             # Entry point
├── Dockerfile          # Build ARM64
└── docker-compose.yml  # Configuracao
```

## Troubleshooting

### Terminal SSH nao conecta

1. Verifique se o usuario existe: `id device`
2. Verifique permissoes da chave: `ls -la /home/device/.ssh/`
3. Teste conexao SSH local: `ssh -i /home/device/.ssh/id_ed25519 device@localhost`

### Stream nao inicia

1. Verifique conectividade com camera: `ping <camera-ip>`
2. Teste RTSP: `ffprobe rtsp://user:pass@camera-ip/stream`
3. Verifique logs: `docker logs beachvar-device-device-1`

### Alta utilizacao de CPU

O FFmpeg consome recursos. Para multiplas cameras, considere:
- Reduzir resolucao/bitrate
- Usar hardware encoding (h264_v4l2m2m no Pi)
