# Проверка соответствия ТЗ Voice Router

Дата проверки: 23 сентября 2026 года. Проверка выполнена по приложенному ТЗ Halyk Bank / HackAlem AI и текущему состоянию репозитория.

## Must-have

| Требование ТЗ | Статус | Проверяемое подтверждение |
| --- | --- | --- |
| Голосовое взаимодействие в вебе | Выполнено в коде | `/voice/`, WebRTC GPT-Live, WebSocket fallback, live captions, голосовой ответ, barge-in; unit/browser tests. Физический микрофон и слышимость аудио нужно один раз проверить вручную на машине демо. |
| Выбор сценария на LLM-слое | Выполнено | Agents SDK Router возвращает типизированный `RoutingDecision`; encoder intent classifier и таблица соответствия реплик отсутствуют. |
| Корректность выбора | Выполнено с измерением | Live: 104/104 технически успешных запросов, primary accuracy 97.1%; 13/13 multi-intent реплик имеют full match. Финальные 10 скрытых реплик доступны только жюри. |
| Панель трассировки | Выполнено | После хода UI показывает transcript, scenario/title, confidence, observable reason, alternatives, pending topics, workflow/action trace и timings по этапам. |
| Русский, казахский и mixed | Выполнено | Routing schema и prompts поддерживают `ru`, `kk`, `mixed`; starter eval содержит все три группы; deterministic tests покрывают переключение языка. |
| Реальный ввод и обработка данных | Выполнено | Микрофон/текст поступают в API, LLM принимает содержательное routing-решение, активный каталог читается из PostgreSQL на каждом ходе. |
| Запуск одной командой | Выполнено | `make up` запускает frontend, backend и PostgreSQL; backend startup seed'ит пустую БД. |

## Ограничения и безопасность

| Требование | Статус | Реализация |
| --- | --- | --- |
| Не угадывать при неуверенности | Выполнено | Confidence threshold, `clarify`, alternatives; handoff после настраиваемого числа действительно неуверенных маршрутизаций. |
| Explainability | Выполнено | Короткое наблюдаемое обоснование и alternatives видны в trace; скрытые chain-of-thought не запрашиваются и не показываются. |
| Подтверждение необратимых действий | Выполнено | 14 сценариев с confirmation; preview до 9 irreversible adapters; только explicit yes/иә запускает `simulate`. |
| Синтетические данные и приватность | Выполнено | Стартовый набор не содержит реальных клиентов; backend fixture read-only; synthetic IDs session-scoped. Пользователь всё равно не должен вводить реальные персональные данные в публичное демо. |
| Нет хардкода проверочных реплик | Выполнено | Prompt строится из текущего каталога; scenario IDs не зашиты в routing logic; unknown references отклоняются схемой. |
| Передача оператору при невозможности продолжить | Частично | Handoff condition и контекст реализованы, но фактической телефонии/очереди живого оператора нет; это явно показано пользователю. |

## Опциональные пункты

| Опция ТЗ | Статус |
| --- | --- |
| Контекст, смена темы и возврат | Выполнено: active/pending scenarios и отдельное состояние workflow на сценарий. |
| Переспрос вместо угадывания | Выполнено. |
| Извлечение параметров из речи | Выполнено с type/enum/regex validation по `slots.json`. |
| Потоковая обработка | Выполнено: native WebRTC voice и WebSocket streaming path. |
| Редактирование каталога без разработчиков | Выполнено: Admin CRUD/import, атомарное сохранение в PostgreSQL. |
| Гибридный быстрый путь | Не реализован: содержательный routing всегда проходит через LLM. |
| Ориентир routing 500 мс | Не достигнут: live mean 4531 мс, p95 7664 мс. Timings честно отображаются. |
| Определение эмоции и адаптация тона | Не реализовано. |
| Агрегированная статистика ошибок супервизора | Частично: trace каждого хода и eval report есть, отдельного dashboard с агрегатами нет. |

## Выполненные проверки

- Python без PostgreSQL: 260 passed, 10 skipped.
- Python с PostgreSQL: 270 passed, включая отдельный startup TDD seed test.
- Frontend: TypeScript, 17 voice/live unit tests и production build passed.
- Playwright: 6/6 desktop/mobile tests passed, включая Admin import и платный live text workflow; console проверен после ожидаемого unauthenticated auth probe.
- First startup на свежей PostgreSQL: 40 scenario rows + 6 остальных JSON settings documents.
- Live evaluation: 104/104 utterances и 40/40 client dialog turns без технических ошибок; utterance primary/exact 97.1%; dialog-turn primary 90%, exact 72.5%.

## Итог

Проект соответствует обязательной части ТЗ и заметно превышает её за счёт settings-driven workflow и безопасных action simulations. Нельзя утверждать, что выполнено «абсолютно всё»: не достигнут опциональный latency target, отсутствуют реальный оператор, emotion detection и отдельная агрегированная supervisor analytics. Эти пробелы не скрыты и не блокируют основную оценку routing по ТЗ.
