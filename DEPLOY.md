# Перенос проекта на Ubuntu-сервер

Команды ниже рассчитаны на сервер `161.104.61.11` и текущую структуру проекта. Для переноса состояния нужны не только файлы из Git: `.env`, `data/` и `telegram_sessions/` находятся вне репозитория. В `.env` уже указана сессия `telegram_sessions/account2`; переносите именно этот файл сессии вместе с проектом.

Перед копированием остановите локальный backend/collector, чтобы SQLite-базы были согласованными. Не используйте одну и ту же Telegram-сессию одновременно на Mac и сервере.

## 1. Подготовка сервера

В уже открытой SSH-сессии:

```bash
apt update
apt install -y rsync
mkdir -p /opt/chats-osint
df -h /
```

Для проекта нужно около 1,1 ГБ, а для Docker-образов дополнительно потребуется свободное место.

## 2. Перенос с Mac

В новом локальном терминале (не в SSH-сессии) выполните:

```bash
cd "/Users/egor/Desktop/Work ВК/Chats OSINT "
rsync -azP \
  -e 'ssh -i /Users/egor/.ssh/id_ed25519_153_80_193_82 -o IdentitiesOnly=yes' \
  --exclude='.git/' --exclude='node_modules/' \
  --exclude='.venv/' --exclude='.venv-local/' \
  --exclude='dist/' --exclude='__pycache__/' \
  --exclude='.pytest_cache/' --exclude='.ruff_cache/' \
  --exclude='.DS_Store' --exclude='*.pyc' \
  ./ root@161.104.61.11:/opt/chats-osint/
```

Каталог проекта занимает около 1,1 ГБ, включая исторические данные и резервные копии. Если SSH прервёт передачу, повторите ту же команду `rsync`: она продолжит с уже перенесёнными файлами. Не используйте `--delete`, если на сервере появились новые данные.

## 3. Сборка и запуск на сервере

На сервере проверьте Docker Compose:

```bash
docker compose version
```

Если команды нет и Docker ещё не установлен, выполните на сервере команды из [официальной инструкции для Ubuntu](https://docs.docker.com/engine/install/ubuntu/) (Ubuntu 26.04 поддерживается):

```bash
apt update
apt install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
```

Если `docker` уже установлен, но `docker compose` не работает, проверьте конфликтующие пакеты по официальной инструкции перед установкой. Затем:

```bash
cd /opt/chats-osint
chmod 600 .env telegram_sessions/*.session
chmod 700 telegram_sessions
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/system/status
```

Если backend не запускается, проверьте `docker compose logs --tail=100 backend`. Если Telegram-сессия потеряла авторизацию, остановите backend и войдите заново:

```bash
docker compose stop backend
docker compose run --rm backend python -m app.cli.telegram_check login
docker compose up -d backend
```

## 4. Подключение всех 92 каналов

После запуска backend, в `/opt/chats-osint` выполните одну команду:

```bash
python3 scripts/connect_izhevsk.py
```

Скрипт читает `telegram_channels.txt`, подключает все 92 канала для мониторинга и сообщает о тех, которые недоступны аккаунту. Аккаунт Telegram должен уже состоять в каналах: API сопоставляет имена только с его диалогами и сам в каналы не вступает. Повторный запуск обновляет существующие источники без дублей. Новые источники начинают сбор с текущего сообщения; старая история автоматически не загружается.

Для предварительного просмотра распределения по городам: `python3 scripts/connect_izhevsk.py --dry-run`. Явно подписанные каналы Глазова, Сарапула, Можги и Воткинска получают свои города. Для остальных по умолчанию указан Ижевск; проверьте географию региональных каналов в интерфейсе.

## 5. Публичный доступ

Frontend публикуется на порту 80 сервера. Nginx внутри frontend-контейнера передаёт `/api` и WebSocket на backend по внутренней Docker-сети; backend наружу не публикуется.

После обновления кода и пересборки откройте:

```text
http://161.104.61.11
```

Если страница не открывается, разрешите входящий TCP-порт 80 в панели облачного провайдера и на файрволе сервера.
