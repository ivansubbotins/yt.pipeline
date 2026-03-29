# YouTube Pipeline — Команды для бота

## Подключение
```
BASE_URL: https://yt.subbotin.digital/api/v1
AUTH: Authorization: Bearer ak_ytpipe_prod_2026_xK9mN4pQ7rS2
FORMAT: JSON. Ответ всегда {ok: bool, data: {}, error?: {code, message}}
```

## Быстрый справочник

### Проекты
```
GET    /projects                          — список проектов
POST   /projects  {topic, channel_id?}    — создать проект
GET    /projects/:id                      — детали проекта
DELETE /projects/:id                      — удалить проект
```

### Pipeline (шаги выполняются последовательно)
```
Шаги: research → sources → content_plan → references → script → teleprompter → covers → description → [shooting] → [editing] → publish

GET    /projects/:id/status                    — статус всех шагов
POST   /projects/:id/run                       — запустить все авто-шаги (5-15 мин)
POST   /projects/:id/steps/{step}/run          — запустить один шаг
POST   /projects/:id/steps/{step}/reset        — сбросить шаг
```

### Выбор (обязательно перед следующими шагами)
```
GET    /projects/:id/angle                     — углы подачи из research
PUT    /projects/:id/angle  {angle, angleIndex} — выбрать угол (ПЕРЕД content_plan!)
GET    /projects/:id/titles                    — заголовки из content_plan
PUT    /projects/:id/titles {titles, selectedIndices} — выбрать заголовки
GET    /projects/:id/hook                      — варианты хуков
PUT    /projects/:id/hook   {hookIndex}        — выбрать хук
```

### Источники (добавлять ПЕРЕД запуском sources step)
```
GET    /projects/:id/sources                   — список
POST   /projects/:id/sources {type:"youtube", url:"..."} — YouTube видео
POST   /projects/:id/sources {type:"url", url:"..."}     — статья (web scraping)
POST   /projects/:id/sources {type:"text", content:"..."} — текст
DELETE /projects/:id/sources/:sourceId         — удалить
```

### Готовый контент
```
GET    /projects/:id/script                    — сценарий (блоки с таймкодами)
GET    /projects/:id/teleprompter              — текст суфлёра (слово в слово)
GET    /projects/:id/description               — описание YouTube + теги
GET    /projects/:id/covers                    — список обложек (URLs для скачивания)
```

### Ручные этапы + публикация
```
POST   /projects/:id/shooting-done             — съёмка завершена
POST   /projects/:id/editing-done {video_file?} — монтаж завершён
POST   /projects/:id/publish {schedule?, playlist_id?, category_id?} — опубликовать
GET    /playlists                              — плейлисты канала
```

### Сплит-тесты
```
POST   /projects/:id/splittest                 — запустить A/B тест
GET    /projects/:id/splittest                 — статус теста
POST   /projects/:id/splittest/stop {method:"auto"} — остановить тест
```

### Каналы
```
GET    /channels                               — список каналов
GET    /channels/default                       — основной канал
GET    /channels/:id                           — контекст канала
```

### Вебхуки
```
POST   /webhooks {url, events[], secret?}      — зарегистрировать
GET    /webhooks                               — список
DELETE /webhooks/:id                           — удалить

События: step.completed, step.failed, project.created, publish.completed, splittest.completed
```

## Типичный сценарий бота

```
1. POST /projects {topic: "тема"}               → получить project_id
2. POST /projects/:id/steps/research/run         → подождать завершения
3. GET  /projects/:id/status                     → проверить research.data
4. PUT  /projects/:id/angle {angle: "лучший угол"} → выбрать
5. POST /projects/:id/sources {type:"youtube", url:"..."} → добавить источники
6. POST /projects/:id/steps/sources/run          → извлечь факты
7. POST /projects/:id/run                        → запустить content_plan → description
8. GET  /projects/:id/teleprompter               → получить текст
9. GET  /projects/:id/covers                     → получить обложки
10. POST /projects/:id/publish {schedule: "ISO"}  → запланировать
```

## Важно
- Угол подачи ОБЯЗАТЕЛЬНО выбрать перед content_plan
- Pipeline шаги выполняются строго по порядку
- run-all запускает шаги 1-7, shooting/editing — ручные
- Publish требует video file (загруженный через editing-done)
- Rate limit: 60 req/min
