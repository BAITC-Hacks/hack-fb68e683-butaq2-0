# Настройка и демонстрация Butaq

## 1. Подготовить окружение

Нужны Docker Desktop/Engine с Compose и OpenAI API key. В корне проекта:

```bash
cp .env.example .env
```

В `.env` обязательно заменить:

- `V2V_API_KEY` — рабочий API key;
- `ROUTER_ADMIN_TOKEN` — длинный случайный секрет;
- `POSTGRES_PASSWORD` — пароль локальной PostgreSQL;
- для публичного домена — `FRONTEND_ORIGINS` и точный `ROUTER_WEBAUTHN_ORIGIN`.

Рекомендуемые параметры демо уже заданы в примере:

```dotenv
ROUTER_MODEL=gpt-5.6-terra
ROUTER_CONFIDENCE_THRESHOLD=0.65
ROUTER_WORKFLOW_ENABLED=true
ROUTER_MAX_UNCERTAIN_TURNS=2
MULTI_AGENT_LIVE_MODEL=gpt-live-1
MULTI_AGENT_LIVE_VOICE=marin
```

`simulation_mode` намеренно фиксирован значением `simulate`: произвольный исполняемый код и реальные необратимые операции запрещены.

## 2. Запустить одной командой

```bash
make up
docker compose ps
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/router/scenarios
```

При startup backend выполняет миграции. Если таблица сценариев пуста, одна атомарная транзакция записывает все JSON-данные `case_2/voice_router_dataset`: 40 scenarios, 43 slots, 31 actions, knowledge base, mock backend, 104 dev utterances и 10 dialogs. Параллельные backend workers защищены PostgreSQL advisory lock.

Повторный запуск не перезаписывает каталог, промпты и настройки администратора. `make down` сохраняет volume PostgreSQL.

## 3. Настроить активный каталог

Открыть `http://localhost:3000/admin/`, войти с `ROUTER_ADMIN_TOKEN` и проверить:

- Configuration: workflow включён, confidence threshold и число неуверенных ходов подходят для демо;
- Scenarios: отображается 40 исходных сценариев;
- Import data: доступны все семь JSON-файлов.

Если база уже содержала старый или пользовательский каталог, startup намеренно его не заменит. Для возврата к официальному набору использовать атомарный импорт в Admin либо API:

```bash
curl -sS http://localhost:8000/router/admin/catalog/import-files \
  -H "X-Admin-Token: $ROUTER_ADMIN_TOKEN" \
  -F scenarios=@case_2/voice_router_dataset/scenarios.json \
  -F slots=@case_2/voice_router_dataset/slots.json \
  -F actions=@case_2/voice_router_dataset/actions.json \
  -F knowledge_base=@case_2/voice_router_dataset/knowledge_base.json \
  -F mock_backend=@case_2/voice_router_dataset/mock_backend.json \
  -F dev_utterances=@case_2/voice_router_dataset/dev_utterances.json \
  -F dialogs_sample=@case_2/voice_router_dataset/dialogs_sample.json
```

Импорт заменяет все перечисленные части в одной транзакции, но сохраняет model, prompts и workflow settings.

## 4. Показать основной сценарий

Открыть `http://localhost:3000/voice/`, разрешить микрофон и сказать: «Хочу оформить ОГПО». Далее дать запрошенные значения региона, типа автомобиля, ИИН водителей, госномера и телефона. Ожидаемый путь:

1. Router выбирает сценарий из текущего каталога PostgreSQL.
2. Workflow спрашивает по одному отсутствующему обязательному slot.
3. Read/calculation adapters формируют результаты.
4. Перед необратимым действием интерфейс показывает preview и требует явное «да»/«иә».
5. После подтверждения action trace содержит `simulate`; mock backend не меняется.
6. При смене темы первая тема попадает в pending и может быть продолжена позднее.

Справа должны отображаться scenario, confidence, observable reason, alternatives, slots, action trace и timings. Текстовое поле — резервный канал, а не замена микрофона.

## 5. Выполнить проверки

```bash
# Python + PostgreSQL
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
TEST_DATABASE_URL=postgresql+psycopg://butaq:$POSTGRES_PASSWORD@localhost:5433/butaq \
  python -m pytest -q

# Frontend unit/type/build
cd frontend
npm ci
npm run typecheck
npm run test:voice
npm run build

# Browser QA; backend/frontend должны быть запущены
ROUTER_ADMIN_TOKEN=... RUN_LIVE_E2E=1 npm run test:e2e
```

Платный routing benchmark:

```bash
python case_2/voice_router_dataset/evaluate_live.py \
  --base-url http://localhost:8000 \
  --dialogs \
  --report /tmp/butaq-live-report.json
```

## 6. Границы демо

- Результаты действий синтетические и хранятся только в памяти сессии.
- Handoff формирует контекст, но не соединяет с реальным оператором.
- Состояние диалога теряется при restart backend.
- Реальная производительность зависит от сети и модели; ориентир ТЗ 500 мс не гарантируется.
