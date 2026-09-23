import type { TurnResult } from "./voice-api";

/** Missing measurements stay null in both UI and exported jury evidence. */
export function measuredTimings(turn: TurnResult): Record<string, number | null> {
  return { ...turn.timings, stt_ms: turn.transport === "live" ? null : turn.timings.stt_ms,
    tts_ms: turn.transport === "live" ? null : turn.timings.tts_ms,
    speech_end_to_audio_ms: null }; // No reliable utterance/playback correlation yet.
}

export function exportTrace(turns: TurnResult[]): string {
  return JSON.stringify({ schema_version: 1, exported_at: new Date().toISOString(), execution_mode: "read_only", turns: turns.map((turn) => ({ ...turn, audio_base64: undefined, timings: measuredTimings(turn) })) }, null, 2);
}

export const voiceCopy = {
  ru: {
    idle: "Готов к разговору", connecting: "Подключаем микрофон…", listening: "Слушаю — говорите спокойно", processing: "Проверяю данные…", speaking: "Butaq отвечает — можно перебить",
    start: "Поговорить с Butaq", end: "Завершить", interrupt: "Перебить", send: "Отправить", reset: "Новый разговор", mute: "Выключить микрофон", unmute: "Включить микрофон", muted: "Микрофон выключен",
    hint: "Говорите на русском или казахском. Можно перебить ответ голосом или кнопкой. Наушники уменьшают эхо. Используйте только вымышленные данные.",
    input: "Или напишите сообщение…", sending: "Отправляем…", captions: "Распознанная речь", you: "Вы", verified: "Проверенный ответ backend", history: "Реплики и решения", trace: "Трассировка", export: "Скачать JSON", alternatives: "Альтернативы", none: "Нет", pending: "Отложенные темы", parameters: "Извлечённые параметры", language: "Язык ответа", transition: "Смена темы", unmeasured: "Не измерено", confidence: "Оценка модели, не вероятность", handoff: "Нужен специалист. Соединение с оператором пока не реализовано.", readonly: "Демонстрация: операции не исполняются, только чтение данных и обсуждение действий.", fallback: "Отправить текст без стриминга", exportHint: "Экспорт содержит текст и параметры последних 10 реплик. Не используйте реальные персональные данные.",
  },
  kk: {
    idle: "Сөйлесуге дайынмын", connecting: "Микрофон қосылуда…", listening: "Тыңдап тұрмын — асықпай сөйлеңіз", processing: "Деректерді тексеріп жатырмын…", speaking: "Butaq жауап беруде — сөзін бөлуге болады",
    start: "Butaq-пен сөйлесу", end: "Аяқтау", interrupt: "Сөзін бөлу", send: "Жіберу", reset: "Жаңа әңгіме", mute: "Микрофонды өшіру", unmute: "Микрофонды қосу", muted: "Микрофон өшірулі",
    hint: "Қазақша немесе орысша сөйлеңіз. Жауапты дауыспен немесе батырмамен бөлуге болады. Құлаққап жаңғырықты азайтады. Тек ойдан шығарылған деректерді пайдаланыңыз.",
    input: "Немесе хабарлама жазыңыз…", sending: "Жіберілуде…", captions: "Танылған сөйлеу", you: "Сіз", verified: "Backend тексерген жауап", history: "Репликалар мен шешімдер", trace: "Трассировка", export: "JSON жүктеу", alternatives: "Балама сценарийлер", none: "Жоқ", pending: "Кейінге қалдырылған тақырыптар", parameters: "Анықталған параметрлер", language: "Жауап тілі", transition: "Тақырып ауысуы", unmeasured: "Өлшенбеген", confidence: "Модель бағасы, ықтималдық емес", handoff: "Маманның көмегі қажет. Операторға қосу әзірге қолжетімсіз.", readonly: "Демонстрация: операциялар орындалмайды, тек деректерді оқу және әрекеттерді талқылау.", fallback: "Мәтінді стримингсіз жіберу", exportHint: "Экспортта соңғы 10 репликаның мәтіні мен параметрлері бар. Нақты жеке деректерді пайдаланбаңыз.",
  },
};
export type VoiceLocale = keyof typeof voiceCopy;
