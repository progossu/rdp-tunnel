# RdpNet 🛡️
> Легковесная изолированная сеть для RDP через Ubuntu VPS (аналог ZeroTier без драйверов)
> Lightweight isolated multi-tenant RDP tunnel over Ubuntu VPS (zero drivers, zero admin rights).

---

## 🇷🇺 Русская инструкция

### Описание
**RdpNet** — это специализированное решение для организации удалённого доступа по RDP (Remote Desktop) к компьютерам, находящимся в интернете за NAT и роутерами, с использованием вашего Ubuntu VPS сервера.

- **Без драйверов и прав Администратора:** в отличие от VPN и ZeroTier, не требует установки `wintun.dll` или прав администратора на Windows.
- **Изолированные сети:** создавайте отдельные изолированные сети (например, «Офис Бухгалтерия», «Домашний ПК», «Серверная»). Устройства из разных сетей изолированы на 100%.
- **Веб-панель управления на VPS (порт 8080):** наглядный веб-интерфейс со списком сетей, созданием ключей доступа, мониторингом подключённых ПК в реальном времени и кнопкой генерации готовых команд в один клик.
- **Нативный компактный клиент для Windows (`rdp-agent.exe`):** написан на Ассемблере / C++ (размер всего ~3 КБ).

---

### 1. Установка на Ubuntu VPS (в 1 команду)

Подключитесь к вашему Ubuntu VPS по SSH и выполните:

```bash
git clone https://github.com/your-repo/rdp-tunnel.git /tmp/rdp-tunnel
cd /tmp/rdp-tunnel
sudo bash scripts/install_vps.sh
```

Скрипт автоматически:
1. Установит необходимые компоненты.
2. Настроит systemd-сервис `rdpnet` и включит автозагрузку при старте VPS.
3. Откроет порты `8443` (ретранслятор) и `8080` (веб-панель) в фаерволе UFW.
4. Запустит сервис и выведет ссылку на Веб-панель: `http://<IP_ВАШЕГО_VPS>:8080`.

---

### 2. Использование Веб-интерфейса

1. Откройте в браузере: `http://<IP_ВАШЕГО_VPS>:8080`.
2. Нажмите **«+ New Network»**, укажите название сети (например: `Офис`).
3. Нажмите кнопку **«Connect»** напротив нужной сети: веб-интерфейс покажет готовую команду запуска и кнопку скачивания файла `rdp-agent.ini`.

---

### 3. Запуск на компьютерах Windows

#### Шаг А: На удалённом компьютере, к которому подключаемся (Хост RDP):
Запустите `rdp-agent.exe` в режиме хоста:
```cmd
rdp-agent.exe -server <IP_VPS>:8443 -net <NETWORK_ID> -key <KEY> -host -name "Office-PC-1"
```
*(Или просто положите рядом файл `rdp-agent.ini`, скачанный из Веб-панели, и запустите `rdp-agent.exe`).*

#### Шаг Б: На вашем компьютере, с которого подключаемся (Клиент / Просмотр):
Запустите `rdp-agent.exe` в режиме клиента:
```cmd
rdp-agent.exe -server <IP_VPS>:8443 -net <NETWORK_ID> -key <KEY> -viewer -target "Office-PC-1"
```
Клиент откроет локальный защищённый порт `127.0.0.1:33890`.

#### Шаг В: Подключение по RDP
Откройте стандартную программу Windows **«Подключение к удаленному рабочему столу»** (`mstsc.exe`), введите адрес:
```
localhost:33890
```
и нажмите **Подключить**! Вы подключены к удалённому компьютеру через зашифрованный туннель на максимальной скорости.

---

## 🇬🇧 English Documentation

### Architecture
- **Server:** Python 3.10+ async daemon running Relay Core on `:8443` and Web Dashboard on `:8080`.
- **Client:** Native standalone Windows executable `rdp-agent.exe` (< 15 KB, pure Win32 ASM / C++).
- **Security:** Strict network isolation by `network_id` and SHA-256 tokens. Wire-speed RDP streaming with end-to-end TLS / CredSSP.

### Quick Start
1. Run `sudo bash scripts/install_vps.sh` on your Ubuntu VPS.
2. Open `http://<VPS_IP>:8080` in your browser.
3. Create your network, download `rdp-agent.ini`, and start `rdp-agent.exe` on Windows.
