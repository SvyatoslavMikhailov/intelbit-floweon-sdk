# intelbit-floweon-sdk

SDK разработчика плагинов платформы **Интелбит.Фловеон** — базовые контракты для
**коннекторов**, **трансформеров** и **нотификаторов**, плюс runner для subprocess-изоляции
плагинов. Пакет — `floweon_sdk`. Лицензия **Apache 2.0**.

## Что внутри

| Модуль | Назначение |
|--------|------------|
| `connector` | `BasePlugin`, `ConnectorPlugin` (ABC: `read`/`write` + lifecycle), `PluginContext`, `PluginHealth` |
| `transformer` | `TransformerPlugin` |
| `notifier` | `NotifierPlugin` |
| `manifest` | `PluginManifest`, `PluginType` |
| `runner` | `PluginRunner`, circuit breaker, ошибки вызова/таймаута |

Контракт плагина — ADR-006 (subprocess-изоляция): плагин может быть убит в любой момент;
соединения между вызовами не держим; методы idempotent.

## Установка

На старте SDK распространяется как **публичный git-источник по тегу** (без PyPI):

```toml
# pyproject.toml потребителя
[project]
dependencies = ["intelbit-floweon-sdk"]

[tool.uv.sources]
intelbit-floweon-sdk = { git = "https://github.com/SvyatoslavMikhailov/intelbit-floweon-sdk", tag = "v0.2.0" }
```

Ставится без токена и работает в форк-PR.

## Разработка

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Происхождение

Вынесено чистой копией из `intelbit-floweon-monorepo/packages/sdk` (v0.0.1) в отдельный
публичный репозиторий — чтобы экосистема видела SDK, а коннекторы ставили его без доступа
к приватному монорепо. История монорепо не переносилась.

## Roadmap

- Публикация на **PyPI** (`pip install intelbit-floweon-sdk`) — отдельный будущий шаг.
