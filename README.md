# BeachVar Device

Software para dispositivo de captura de video (Raspberry Pi) do sistema BeachVar.

## Funcionalidades

- Streaming de cameras RTSP via FFmpeg
- HLS output para Cloudflare Stream
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

### 2. Configurar variaveis de ambiente

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

### 3. Iniciar o container

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
    volumes:
      - /tmp/hls:/tmp/hls  # tmpfs em producao (2GB RAM)
    ports:
      - "8080:8080"
    restart: unless-stopped
```

### HLS Storage

Em producao, os segmentos HLS sao armazenados em RAM (tmpfs) para:
- Melhor performance de I/O
- Menor desgaste do cartao SD
- Janela DVR de 4 minutos (120 segmentos de 2s)

O setup automatico (`/api/setup`) configura o tmpfs com 2GB.

### Variaveis de Ambiente

| Variavel | Descricao | Default |
|----------|-----------|---------|
| `DEVICE_ID` | Identificador unico do device | `unknown` |
| `DEVICE_TOKEN` | Token de autenticacao com backend | - |
| `BACKEND_URL` | URL da API backend | - |
| `GATEWAY_URL` | URL do gateway WebSocket | - |

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

### Stream nao inicia

1. Verifique conectividade com camera: `ping <camera-ip>`
2. Teste RTSP: `ffprobe rtsp://user:pass@camera-ip/stream`
3. Verifique logs: `docker logs beachvar-device-device-1`

### Alta utilizacao de CPU

O FFmpeg consome recursos. Para multiplas cameras, considere:
- Reduzir resolucao/bitrate
- Usar hardware encoding (h264_v4l2m2m no Pi)
