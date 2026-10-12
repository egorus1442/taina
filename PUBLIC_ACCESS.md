# Доступ к интерфейсу по IP

Публичный вход работает через `https://161.104.61.11`. Отдельный Nginx-шлюз требует имя пользователя и пароль перед тем, как передать запрос к интерфейсу или API. Основные контейнеры продолжают слушать только `127.0.0.1`.

Сертификат Let’s Encrypt для IP действует примерно шесть дней, поэтому на сервере установлен таймер `chats-certbot-renew.timer`. Он проверяет сертификат дважды в день и перезагружает шлюз после обновления. Для проверки:

```bash
systemctl status chats-certbot-renew.timer
systemctl list-timers chats-certbot-renew.timer
/opt/chats-certbot/bin/certbot certificates
docker exec chats-osint-gateway nginx -t
```

Для обновления приложения:

```bash
cd /home/chats-osint
git pull --ff-only
docker compose -f docker-compose.yml -f docker-compose.public.yml up -d --build
```

Если меняется пароль, создайте новый bcrypt-хеш в `/home/chats-osint/public/.htpasswd` и перезагрузите шлюз. Файл с хешем исключён из Git. Порты 80 и 443 должны быть разрешены у облачного провайдера: 80 нужен для выпуска и обновления сертификата, 443 — для браузера.
