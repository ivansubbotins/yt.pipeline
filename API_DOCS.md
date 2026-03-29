# YouTube Pipeline — REST API v1

## Подключение

**Base URL:** `https://yt.subbotin.digital/api/v1`

**Аутентификация:** Bearer token в заголовке
```
Authorization: Bearer ak_ytpipe_prod_2026_xK9mN4pQ7rS2
```

**Формат ответа:**
```json
{
  "ok": true,
  "data": { ... },
  "meta": { "request_id": "req_abc123", "timestamp": "2026-03-29T12:00:00Z" }
}
```

**Ошибки:**
```json
{
  "ok": false,
  "error": { "code": "NOT_FOUND", "message": "Project not found" }
}
```

**Rate limit:** 60 запросов/мин. Заголовки: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

---

## Полный цикл создания видео

```
1. POST /projects                        → создать проект из темы
2. POST /projects/:id/steps/research/run → провести исследование YouTube
3. GET  /projects/:id/status             → получить результаты research
4. PUT  /projects/:id/angle              → выбрать угол подачи
5. POST /projects/:id/sources            → добавить источники (YouTube URL, статьи)
6. POST /projects/:id/steps/sources/run  → извлечь факты из источников
7. POST /projects/:id/run                → запустить весь pipeline (content_plan → description)
8. GET  /projects/:id/titles             → получить сгенерированные заголовки
9. PUT  /projects/:id/titles             → выбрать заголовки
10. GET /projects/:id/teleprompter       → получить текст для суфлёра
11. GET /projects/:id/covers             → получить обложки
12. POST /projects/:id/shooting-done     → отметить съёмку завершённой
13. POST /projects/:id/editing-done      → отметить монтаж завершённым
14. POST /projects/:id/publish           → опубликовать на YouTube
15. POST /projects/:id/splittest         → запустить A/B тест
```

---

## Эндпоинты

### Проекты

#### `GET /projects`
Список всех проектов.

**Ответ:**
```json
{
  "data": {
    "projects": [
      {
        "id": "20260329-topic-slug",
        "topic": "Тема видео",
        "current_step": "content_plan",
        "created_at": "2026-03-29T10:00:00Z"
      }
    ]
  }
}
```

#### `POST /projects`
Создать новый проект.

**Тело запроса:**
```json
{
  "topic": "Как AI меняет маркетинг",
  "channel_id": "optional-channel-id"
}
```

**Ответ:**
```json
{
  "data": {
    "project_id": "20260329-kak-ai-menyaet-marketing",
    "output": "Создан проект: 20260329-kak-ai-menyaet-marketing"
  }
}
```

#### `GET /projects/:id`
Получить полное состояние проекта (все шаги, данные, статусы).

#### `DELETE /projects/:id`
Удалить проект и все его файлы.

---

### Pipeline

#### `GET /projects/:id/status`
Статус pipeline — текущий шаг и статусы всех шагов.

**Ответ:**
```json
{
  "data": {
    "project_id": "20260329-topic",
    "current_step": "script",
    "steps": {
      "research": { "status": "completed", "data": { ... } },
      "sources": { "status": "completed", "data": { ... } },
      "content_plan": { "status": "completed", "data": { ... } },
      "references": { "status": "completed" },
      "script": { "status": "in_progress" },
      "teleprompter": { "status": "pending" },
      "covers": { "status": "pending" },
      "description": { "status": "pending" },
      "shooting": { "status": "pending" },
      "editing": { "status": "pending" },
      "publish": { "status": "pending" }
    }
  }
}
```

**Статусы шагов:** `pending`, `in_progress`, `completed`, `waiting`, `approved`, `failed`

#### `POST /projects/:id/run`
Запустить все авто-шаги (research → description). Долгая операция — может занять 5-15 минут.

#### `POST /projects/:id/steps/:step/run`
Запустить один конкретный шаг.

**Доступные шаги:** `research`, `sources`, `content_plan`, `references`, `script`, `teleprompter`, `covers`, `description`

#### `POST /projects/:id/steps/:step/reset`
Сбросить шаг в статус `pending` для перезапуска.

---

### Выбор контента

#### `GET /projects/:id/titles`
Получить сгенерированные заголовки.

#### `PUT /projects/:id/titles`
Выбрать заголовки.

**Тело запроса:**
```json
{
  "titles": ["Заголовок 1", "Заголовок 2"],
  "selectedIndices": [0, 2]
}
```

#### `GET /projects/:id/angle`
Получить исследованные углы подачи.

#### `PUT /projects/:id/angle`
Выбрать угол подачи. **Обязательно перед запуском content_plan.**

**Тело запроса:**
```json
{
  "angle": "Драматическая бизнес-война между X5 и Магнитом",
  "angleIndex": 0
}
```

#### `GET /projects/:id/hook`
Получить варианты хуков.

#### `PUT /projects/:id/hook`
Выбрать хук.

**Тело запроса:**
```json
{ "hookIndex": 1 }
```

---

### Источники

#### `GET /projects/:id/sources`
Список источников проекта. После research автоматически заполняется прорывными YouTube-видео.

#### `POST /projects/:id/sources`
Добавить источник.

**YouTube видео:**
```json
{
  "type": "youtube",
  "url": "https://youtube.com/watch?v=xxx"
}
```

**Статья (web scraping):**
```json
{
  "type": "url",
  "url": "https://example.com/article"
}
```

**Текст вручную:**
```json
{
  "type": "text",
  "title": "Мои заметки",
  "content": "Текст с фактами и данными..."
}
```

**NotebookLM:**
```json
{
  "type": "notebook",
  "notebook_id": "28716bc8-ff45-4709-8889-b8fba4ccdc8d",
  "title": "Мой блокнот"
}
```

#### `DELETE /projects/:id/sources/:sourceId`
Удалить источник.

---

### Генерированный контент

#### `GET /projects/:id/script`
Сценарий видео — блоки с таймкодами, тезисами, визуальными указаниями.

**Ответ:**
```json
{
  "data": {
    "script": {
      "blocks": [
        {
          "block_number": 1,
          "block_type": "hook",
          "name": "Шокирующий хук",
          "duration_seconds": 30,
          "talking_points": ["тезис 1", "тезис 2"],
          "key_phrase": "Дословная фраза для произнесения",
          "visual_direction": "Крупный план, жест удивления"
        }
      ],
      "total_duration_minutes": 14
    }
  }
}
```

#### `GET /projects/:id/teleprompter`
Текст для телесуфлёра — слово в слово что говорить на камеру.

**Ответ:**
```json
{
  "data": {
    "text": "Полный текст телесуфлёра...",
    "data": {
      "scenes": [
        { "scene_name": "Хук", "teleprompter_text": "Текст сцены..." }
      ],
      "total_word_count": 2100,
      "estimated_read_time_minutes": 14
    }
  }
}
```

#### `GET /projects/:id/description`
SEO-оптимизированное описание для YouTube.

**Ответ:**
```json
{
  "data": {
    "text": "Полное описание с ссылками, тегами, хештегами...",
    "data": {
      "title": "Финальный заголовок",
      "tags": ["тег1", "тег2"],
      "hashtags": ["#хештег1"],
      "timestamps": [{"time": "0:00", "label": "Вступление"}]
    }
  }
}
```

#### `GET /projects/:id/covers`
Список сгенерированных обложек.

**Ответ:**
```json
{
  "data": {
    "covers": [
      { "filename": "thumbnail_1.jpg", "url": "/api/file/PROJECT_ID/thumbnails/thumbnail_1.jpg" },
      { "filename": "thumbnail_2.jpg", "url": "/api/file/PROJECT_ID/thumbnails/thumbnail_2.jpg" }
    ]
  }
}
```

---

### Ручные этапы

#### `POST /projects/:id/shooting-done`
Отметить съёмку завершённой. Переводит pipeline на этап монтажа.

#### `POST /projects/:id/editing-done`
Отметить монтаж завершённым.

**Тело запроса (опционально):**
```json
{
  "video_file": "/path/to/final_video.mp4"
}
```

---

### Публикация

#### `POST /projects/:id/publish`
Опубликовать видео на YouTube.

**Тело запроса:**
```json
{
  "schedule": "2026-04-01T14:00:00Z",
  "playlist_id": "PLxxxxx",
  "category_id": "27"
}
```
Все поля опциональны. Без `schedule` — публикуется сразу (private).

**Ответ:**
```json
{
  "data": {
    "project_id": "20260329-topic",
    "output": "Видео опубликовано: https://youtube.com/watch?v=xxx"
  }
}
```

#### `GET /playlists`
Список плейлистов YouTube-канала.

---

### Сплит-тесты

#### `POST /projects/:id/splittest`
Запустить A/B тест заголовков и обложек.

#### `GET /projects/:id/splittest`
Статус текущего теста — варианты, просмотры, winner.

#### `POST /projects/:id/splittest/stop`
Остановить тест и выбрать победителя.

**Тело запроса:**
```json
{
  "method": "auto",
  "winner_index": 0
}
```
`method`: `auto` (по просмотрам) или `manual` (указать `winner_index`).

---

### Каналы

#### `GET /channels`
Список каналов.

#### `GET /channels/:id`
Контекст канала (автор, ниша, ЦА, ссылки, CTA). Используй `default` для основного канала.

---

### Вебхуки

#### `POST /webhooks`
Зарегистрировать webhook для получения уведомлений.

**Тело запроса:**
```json
{
  "url": "https://your-bot.example.com/callback",
  "events": ["step.completed", "publish.completed"],
  "secret": "your_webhook_secret"
}
```

**Доступные события:**
- `step.completed` — шаг pipeline завершён
- `step.failed` — шаг упал с ошибкой
- `project.created` — новый проект создан
- `publish.completed` — видео опубликовано на YouTube
- `splittest.completed` — A/B тест завершён

**Payload webhook:**
```json
{
  "event": "step.completed",
  "timestamp": "2026-03-29T12:05:00Z",
  "project_id": "20260329-topic",
  "data": {
    "step": "script",
    "status": "completed"
  }
}
```

**Подпись:** заголовок `X-Webhook-Signature: sha256=<HMAC>` (HMAC-SHA256 тела запроса с вашим secret).

#### `GET /webhooks`
Список ваших зарегистрированных webhooks.

#### `DELETE /webhooks/:id`
Удалить webhook.

---

## Коды ошибок

| Код | HTTP | Описание |
|-----|------|----------|
| `UNAUTHORIZED` | 401 | Неверный или отсутствующий API ключ |
| `RATE_LIMITED` | 429 | Превышен лимит запросов (60/мин) |
| `NOT_FOUND` | 404 | Проект или ресурс не найден |
| `INVALID_INPUT` | 400 | Отсутствуют обязательные поля |
| `STEP_FAILED` | 500 | Ошибка выполнения шага pipeline |
| `INTERNAL_ERROR` | 500 | Внутренняя ошибка сервера |

---

## Примеры (curl)

### Создать проект и запустить pipeline
```bash
API_KEY="ak_ytpipe_prod_2026_xK9mN4pQ7rS2"
BASE="https://yt.subbotin.digital/api/v1"

# 1. Создать проект
curl -X POST "$BASE/projects" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"topic": "Как AI меняет маркетинг в 2026"}'

# 2. Запустить research
curl -X POST "$BASE/projects/20260329-kak-ai-menyaet/steps/research/run" \
  -H "Authorization: Bearer $API_KEY"

# 3. Проверить статус
curl "$BASE/projects/20260329-kak-ai-menyaet/status" \
  -H "Authorization: Bearer $API_KEY"

# 4. Выбрать угол
curl -X PUT "$BASE/projects/20260329-kak-ai-menyaet/angle" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"angle": "AI заменит 80% маркетологов к 2027", "angleIndex": 0}'

# 5. Запустить весь pipeline
curl -X POST "$BASE/projects/20260329-kak-ai-menyaet/run" \
  -H "Authorization: Bearer $API_KEY"

# 6. Получить телесуфлёр
curl "$BASE/projects/20260329-kak-ai-menyaet/teleprompter" \
  -H "Authorization: Bearer $API_KEY"

# 7. Опубликовать
curl -X POST "$BASE/projects/20260329-kak-ai-menyaet/publish" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"schedule": "2026-04-01T14:00:00Z"}'
```

### Зарегистрировать webhook
```bash
curl -X POST "$BASE/webhooks" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://my-bot.com/yt-callback", "events": ["step.completed", "publish.completed"]}'
```
