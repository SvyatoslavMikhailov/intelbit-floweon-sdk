# CLAUDE.md — intelbit-river-sdk

Гайд для Claude Code по этому репозиторию.

## Что это

Публичный (Apache 2.0) SDK разработчика плагинов Интелбит:Река. Пакет `river_sdk`.
Базовые контракты `ConnectorPlugin`/`TransformerPlugin`/`NotifierPlugin`, `PluginManifest`,
`PluginRunner` (subprocess-изоляция, circuit breaker). Вынесен чистой копией из
`intelbit-river-monorepo/packages/sdk`.

## Важно

- ==Публичный контракт SDK не менять без необходимости== — на него завязаны коннекторы
  (onec, ozon, bitrix24) и ядро монорепо (`river_core`). Это инструмент экосистемы.
- Контракт плагина — ADR-006: subprocess-изоляция, соединения не держим между вызовами,
  методы idempotent.
- Зависимость минимальна — только `pydantic>=2.9`. Не добавлять тяжёлых зависимостей.

## Команды

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Дистрибуция

На старте — публичный git-тег (`v0.1.0`) через `[tool.uv.sources]`. PyPI — будущий шаг.

---

## LLM Coding Guidelines (Karpathy)

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

*Source: https://github.com/forrestchang/andrej-karpathy-skills/blob/main/CLAUDE.md*
