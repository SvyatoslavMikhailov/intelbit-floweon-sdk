# Changelog

## [0.3.0] — 2026-10-02

### Изменено

- `PluginRunner`: в дочернем процессе — один долгоживущий event loop (поток) вместо
  `asyncio.run` на каждый вызов; состояние плагина (rate limiter клиента, asyncio-примитивы)
  живёт между вызовами.
- Конкурентные вызовы с correlation id вместо одного lock на плагин: семафор
  `max_concurrency` (по умолчанию 4) и быстрая полоса `fast_methods`
  (`subscribe`, `health`, `health_check`) — приём вебхука не ждёт долгую запись.
- `CircuitBreaker`: half-open после `cooldown_sec` (по умолчанию 30) — пробный вызов,
  успех закрывает breaker (раньше — только ручной reset); `health()` отдаёт `breaker`
  (`closed|open|half_open`), `in_flight`, `last_error`.
- Смерть дочернего процесса без ответа (краш, OOM) завершает ожидающие вызовы ошибкой,
  а не таймаутом.

### Добавлено

- `PluginEntrypoint` (`floweon_sdk.entrypoint`): контракт запуска ConnectorPlugin ядром —
  `cls(config)`, `read/write/subscribe/health/start/stop` с одним JSON-аргументом, ошибки
  коннектора возвращаются данными `{"_error": {type, message, transient}}` (не открывают
  circuit breaker), отказ вебхука — `{"accepted": false, "status": 401|400}`.
  Перенесён из ядра Фловеона (`ConnectorAdapter`).

## [0.2.0] — 2026-10-02

### Изменено (breaking)

- Переименование river → floweon (имя продукта Интелбит.Фловеон, D-1 от 21.07.2026):
  пакет `river_sdk` → `floweon_sdk`, dist-имя `intelbit-river-sdk` → `intelbit-floweon-sdk`,
  репозиторий `intelbit-river-sdk` → `intelbit-floweon-sdk`. Меняется import-путь —
  потребителям нужно заменить `river_sdk` на `floweon_sdk`.

## [0.1.0]

- Первый публичный выпуск SDK (тогда — `intelbit-river-sdk`, пакет `river_sdk`;
  прежнее имя продукта — Интелбит:Река).
