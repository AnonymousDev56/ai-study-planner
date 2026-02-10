# AI Study Planner

AI Study Planner — это учебный планировщик для студентов, который помогает организовать занятия, отслеживать прогресс и получать умные подсказки. Проект построен на Flask и хранит данные в SQLite.

## Возможности
- Регистрация и вход пользователей
- Планировщик занятий с приоритетами и заметками
- Предметы с цветными метками
- AI-подсказки (локальная логика)
- Календарь на 7 дней
- Аналитика по предметам и статистика по дням/неделям
- Импорт и экспорт задач в JSON/CSV
- Уведомления в Telegram (создание занятия + напоминания)

## Как запустить локально
1. Установить зависимости и создать виртуальное окружение:
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Создать файл `.env` в корне проекта:
```
SECRET_KEY=your-secret-key
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

3. Запуск сервера:
```powershell
python app.py
```

Открой браузер: `http://127.0.0.1:5000`

## Зависимости
- Python 3.10+
- Flask
- python-dotenv

Список зависимостей находится в `requirements.txt`.

## Что не пушить в GitHub
- `.env` (секреты)
- `.venv/` (виртуальное окружение)
- `*.db` (локальная база данных)
- `__pycache__/` и `*.pyc`

## Структура проекта
- `app.py` — основной сервер Flask
- `templates/` — HTML-шаблоны
- `static/` — CSS и статические файлы
- `requirements.txt` — зависимости

- Live Demo: https://ai-study-planner-opzs.onrender.com/dashboard

- Скриншоты


  <img width="1600" height="899" alt="image" src="https://github.com/user-attachments/assets/cee7316c-d47b-44a8-89c6-5264a516a647" />

  <img width="1600" height="899" alt="image" src="https://github.com/user-attachments/assets/4f952f9d-f50f-4ac3-9f93-abac4ae4544a" />

  <img width="1600" height="899" alt="image" src="https://github.com/user-attachments/assets/e325dfcc-5af0-4bf1-bdfd-efdab5f90e3b" />

  <img width="1919" height="1079" alt="Снимок экрана 2026-02-03 203501" src="https://github.com/user-attachments/assets/a4a59f08-feb3-431c-8608-f67ec43744d6" />

  <img width="1919" height="1079" alt="Снимок экрана 2026-02-03 120558" src="https://github.com/user-attachments/assets/06aa7990-6b6b-4d36-99d1-a07228ed953a" />





  


