# Contributing

Этот репозиторий поставляется под Apache License 2.0. Все контрибуции принимаются на этих же условиях.

## DCO sign-off обязателен

Все коммиты должны быть подписаны:

```bash
git commit -s -m "your commit message"
```

Это добавит `Signed-off-by: Your Name <your.email@example.com>` в commit message — подтверждение,
что вы имеете право вносить эту работу под условиями репозитория.

CI на PR проверяет наличие sign-off через `.github/workflows/dco.yml`.

## Workflow

1. Fork репозиторий.
2. Создать ветку `feature/<short-name>` от `main`.
3. Сделать изменения с DCO sign-off (`-s` флаг каждого commit).
4. Прогнать локально: `uv run ruff check . && uv run mypy && uv run pytest`.
5. Открыть PR в `main`.

## Code style

- Python 3.12+, ruff + mypy strict.
- Public API — с docstrings (Google-style).
- Тесты для всего public API.
- Сообщения коммитов и комментарии в коде — на русском.

## Контакты

vceo@intelbit.ru
