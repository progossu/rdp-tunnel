# RdpNet 🛡️
> Легковесная изолированная сеть для RDP через Ubuntu VPS (без драйверов, прав админа и Python на клиенте)
> Lightweight isolated multi-tenant RDP tunnel over Ubuntu VPS (zero drivers, zero admin rights, standalone 20 KB native Windows client).

---

## 🇷🇺 Быстрый старт (за 2 минуты)

### 1. Установка на Ubuntu VPS (одной командой)

Подключитесь к вашему Ubuntu VPS по SSH и выполните одну команду:

```bash
curl -sSL https://raw.githubusercontent.com/progossu/rdp-tunnel/main/scripts/install_vps.sh | sudo bash
```

Скрипт сам:
- Скачает и установит RdpNet в `/opt/rdpnet`.
- Сгенерирует случайный пароль администратора для веб-панели (`/opt/rdpnet/.env`).
- Настроит автозапуск через `systemd` (`rdpnet.service`) и откроет порты `8443` и `8080` в `ufw`.
- Выведет в терминал ссылку `http://<IP_VPS>:8080`, логин (`admin`) и пароль.

---

### 2. Настройка сетей и скачивание готовых клиентов из Web UI

1. Откройте `http://<IP_VPS>:8080` в браузере и введите логин/пароль.
2. Создайте нужные изолированные сети (**«+ New Network»**), например `Офис`, `Дом`.
3. Нажмите **«Connect»** рядом с сетью -> выберите роль (**Host** или **Viewer**) -> нажмите **«📦 Download Ready ZIP»**.
   - Веб-панель **сама подставит IP вашего VPS**, создаст файл `rdp-agent.ini`, положит автономный `rdp-agent.exe` (~20 КБ, Python не нужен!) и готовые `.bat`-скрипты.

---

### 3. Запуск на Windows (в 1 клик)

#### На удалённом ПК (Хост RDP):
Распакуйте скачанный ZIP-архив для **Host** и запустите:
- `start_host.bat` — разовый запуск в окне;
- или `install_autostart.bat` — установит в автозагрузку текущего пользователя и запустит скрыто в фоне (без прав администратора!).

> **Нестандартный порт RDP (например 5555)?** По умолчанию указано `rdp_port=auto` — агент **сам определит порт RDP из реестра Windows**! При необходимости можно задать порт вручную через `-rdp-port 5555` или в `rdp-agent.ini`.

#### На вашем ПК (Клиент / Viewer):
Распакуйте скачанный ZIP-архив для **Viewer** и запустите `start_rdp.bat` — агент автоматически подключится к туннелю и **сам запустит `mstsc.exe`** на `127.0.0.1:33890`!

---

## 🇬🇧 English Quick Start

1. **Install on Ubuntu VPS (single line):**
   ```bash
   curl -sSL https://raw.githubusercontent.com/progossu/rdp-tunnel/main/scripts/install_vps.sh | sudo bash
   ```
2. **Manage via Web UI (`http://<VPS_IP>:8080`):**
   Log in with the printed `admin` credentials, create isolated networks, and download ready-to-run Host/Viewer `.zip` bundles containing the standalone 20 KB native `rdp-agent.exe` (zero dependencies), pre-filled `rdp-agent.ini`, and launcher scripts.
3. **Run on Windows:**
   - **Host:** Run `start_host.bat` or `install_autostart.bat` (auto-detects custom Windows RDP ports from Registry).
   - **Viewer:** Run `start_rdp.bat` (connects tunnel and auto-launches `mstsc.exe`).
