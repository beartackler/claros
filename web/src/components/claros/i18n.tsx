"use client";

// Simple dictionary i18n. en + ru complete; de/fr/es cover the chrome and fall back to en.
import { createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore } from "react";

export const LANGS = ["en", "de", "fr", "es", "ru"] as const;
export type UiLang = (typeof LANGS)[number];
export const LANG_NAMES: Record<UiLang, string> = { en: "English", de: "Deutsch", fr: "Français", es: "Español", ru: "Русский" };

const en = {
  "app.tagline": "The apprentice that learns the why.",
  "nav.home": "Home",
  "nav.learn": "Learn",
  "nav.inbox": "Inbox",
  "nav.map": "Work Map",
  "nav.skip": "Skip to content",
  "role.label": "I am",
  "role.learner": "Learner",
  "role.expert": "Expert",
  "data.demo": "Demo data",
  "data.demo.hint": "Claros server not reachable — showing demo data",
  "data.live": "Live",
  "lang.label": "Language",

  "cov.ready": "Ready",
  "cov.partial": "Partial",
  "cov.missing": "Missing",
  "cov.ready.long": "Confirmed by experts",
  "cov.partial.long": "Some parts not confirmed yet",
  "cov.missing.long": "Nobody has shown Claros this yet",

  "state.loading": "Loading…",
  "state.error.title": "Something went wrong",
  "state.error.retry": "Try again",
  "state.empty.workflows": "No workflows yet. The first one appears after an expert's capture.",
  "state.empty.requests": "No open requests. When a learner gets stuck on something Claros doesn't know, it lands here.",
  "state.empty.contrib": "You haven't taught Claros anything yet.",

  "home.learner.title": "Stuck on a screen?",
  "home.learner.sub": "Share the window you're working in. Claros finds the workflow and walks you through it on your own case.",
  "home.learner.cta": "Walk me through this",
  "home.learner.hint": "or say “Claros, walk me through this”",
  "home.learner.recent": "Workflows you can learn",
  "home.expert.title": "Your knowledge, where it's needed",
  "home.expert.sub": "Learners asked for these. Claros only takes your time where someone is actually stuck.",
  "home.expert.teach": "Teach Claros something",
  "home.expert.inbox": "Requested by learners",
  "home.expert.contrib": "Workflows you shaped",
  "home.expert.all": "Open inbox",
  "home.experts": "experts",
  "home.openUnknowns": "open questions",
  "home.conflicts": "conflicts",

  "learn.invoke.title": "Walk me through this",
  "learn.invoke.sub": "Claros looks at the window you share and listens to what you're trying to do. Nothing is recorded until you start.",
  "learn.invoke.share": "Share my window",
  "learn.invoke.voice": "Talk to Claros",
  "learn.invoke.intent": "What are you trying to do?",
  "learn.invoke.intent.ph": "e.g. post this supplier invoice",
  "learn.invoke.demo": "Demo: force coverage",
  "learn.sharing": "Sharing",
  "learn.lookup": "Finding this workflow…",
  "learn.lookup.sub": "Matching your screen against the map library and O*NET",
  "learn.ready.title": "I know this one.",
  "learn.ready.sub": "Confirmed by {experts}. Let's do it on your invoice — predict before I tell you.",
  "learn.partial.title": "I know part of this.",
  "learn.partial.sub": "I'll teach what experts confirmed. Where nobody has confirmed it yet, I'll say so instead of guessing.",
  "learn.missing.title": "Nobody has shown me this yet.",
  "learn.missing.sub": "I won't guess at your company's rules. I can ask an expert — they'll see exactly the screen you're on.",
  "learn.missing.ask": "Ask an expert",
  "learn.missing.asked": "Request sent",
  "learn.missing.asked.sub": "{name} will see your screen moment. You'll be notified when the map is ready.",
  "learn.missing.onet": "Looks like this task",
  "learn.start": "Start",
  "learn.step": "Step {n} of {total}",
  "learn.predict.q": "What would you do here?",
  "learn.predict.voice": "Answer by voice",
  "learn.predict.reveal": "Show me",
  "learn.predict.correct": "That's what the expert does.",
  "learn.predict.wrong": "Not quite — here's why.",
  "learn.notConfirmed": "Not confirmed yet",
  "learn.notConfirmed.sub": "No expert has confirmed this step. Ask your lead before acting.",
  "learn.intervene.title": "{name} would stop here.",
  "learn.intervene.ask": "Why do you think?",
  "learn.intervene.ack": "Got it — continue",
  "learn.intervene.flip": "What {name} saw",
  "learn.next": "Next step",
  "learn.mastery.title": "Your run",
  "learn.mastery.sub": "Unaided means you predicted it right without help.",
  "learn.mastery.practice": "Practice next",
  "learn.mastery.practice.sub": "Your weakest step, on a fresh case",
  "learn.lvl.unseen": "Unseen",
  "learn.lvl.caught": "Caught",
  "learn.lvl.hinted": "Hinted",
  "learn.lvl.unaided": "Unaided",
  "learn.conflict.title": "Experts differ here",
  "learn.conflict.ask": "Ask your lead before you choose.",
  "learn.end": "End session",

  "quote.original": "Original",
  "quote.play": "Play the expert's voice",
  "quote.translation": "Translation",

  "capture.preflight.title": "Before Claros watches",
  "capture.preflight.sub": "Just work and talk. Claros asks at most a few questions, only at pauses.",
  "capture.preflight.mic": "Microphone",
  "capture.preflight.mic.ok": "Microphone ready",
  "capture.preflight.mic.test": "Test microphone",
  "capture.preflight.mic.denied": "Microphone blocked — allow it from the browser's address bar.",
  "capture.voice": "Voice",
  "debrief.stop": "Stop here",
  "capture.preflight.window": "Share one window, not your whole screen",
  "capture.preflight.window.sub": "Pick the app window you work in. Other windows, notifications and tabs stay private.",
  "capture.preflight.captured": "Captured",
  "capture.preflight.captured.list": "Keyframes of the shared window (redacted)|What you say while on the record|Field changes Claros sees on screen",
  "capture.preflight.never": "Never captured",
  "capture.preflight.never.list": "Other windows or tabs|Anything said off the record|Raw video or your face|Passwords and masked fields",
  "capture.preflight.lang": "I'll speak",
  "capture.preflight.consent": "I understand what's captured and agree to start.",
  "capture.preflight.start": "Start capture",
  "capture.notebook": "Apprentice's notebook",
  "capture.events": "What Claros saw",
  "capture.asked": "Asked you",
  "capture.saved": "Saved for debrief",
  "capture.saved.sub": "Claros is holding these so you can keep working.",
  "capture.budget": "Question budget",
  "capture.budget.sub": "{used} of {max} in 10 min",
  "capture.why": "Why Claros asked",
  "capture.why.scope": "Knowledge scope",
  "capture.why.hypothesis": "Hypothesis",
  "capture.why.priority": "Priority",
  "capture.why.gate": "Gate",
  "capture.why.gate.pause": "You paused for {s}s — safe moment",
  "capture.why.gate.resolved": "Answered from {source} — not asked",
  "capture.why.gate.deferred": "Saved: you were busy",
  "capture.why.none": "Select a question to see why Claros asked it.",
  "capture.offrecord": "Off the record",
  "capture.offrecord.on": "Off the record — nothing is captured",
  "capture.offrecord.resume": "Back on the record",
  "capture.orb": "Pop out Claros",
  "capture.orb.sub": "Floating orb stays on top of your work",
  "capture.end": "End & debrief",
  "capture.live": "On the record",
  "capture.empty.events": "Work as usual. Screen events appear here as Claros notices them.",

  "debrief.title": "Debrief",
  "debrief.ledger": "Open questions",
  "debrief.ledger.done": "Nothing left to ask. Thank you.",
  "debrief.moment": "This moment",
  "debrief.question": "Claros asks",
  "debrief.answer.voice": "Answer by voice",
  "debrief.answer.type": "Type an answer",
  "debrief.answer.skip": "Not relevant",
  "debrief.answer.submit": "Save answer",
  "debrief.map": "Map assembling",
  "debrief.teachback": "Teach-back",
  "debrief.teachback.sub": "Claros explains the workflow back. Stop it the moment it's wrong.",
  "debrief.teachback.play": "Play teach-back",
  "debrief.correction": "Your corrections",
  "debrief.exam": "Exam: unseen cases",
  "debrief.exam.sub": "Claros predicts what you'd do. Mark each one.",
  "debrief.exam.predicts": "Claros would",
  "debrief.exam.confidence": "confidence",
  "debrief.exam.right": "Right",
  "debrief.exam.wrong": "Wrong",
  "debrief.publish": "Publish map",
  "debrief.publish.sub": "Learners will be taught exactly what you confirmed.",
  "debrief.publish.blocked": "Mark every exam case first",
  "debrief.published": "Published",

  "map.coverage": "Coverage",
  "map.evidence": "Steps with evidence",
  "map.judgments": "Judgments explained",
  "map.guardrails": "Guardrails confirmed",
  "map.lane.steps": "Steps",
  "map.lane.judgment": "Judgment calls",
  "map.lane.guardrails": "Guardrails",
  "map.filmstrip": "Filmstrip",
  "map.screen": "Screen moment",
  "map.decision": "Decision",
  "map.reason": "Reason",
  "map.context": "Context notes",
  "map.counterfactual": "Would change if",
  "map.variants": "Variants",
  "map.conflict": "Experts differ here",
  "map.approve": "Approved",
  "map.export": "Export",
  "map.export.skill": "SKILL.md",
  "map.export.mcp": "Copy MCP URL",
  "map.export.json": "JSON",
  "map.copied": "Copied",
  "map.onet": "O*NET",
  "map.version": "v{n}",
  "map.owner": "Ask",
  "map.select": "Select a step to see its evidence",
  "map.unconfirmed": "Not confirmed yet",
  "map.notFound": "This map doesn't exist yet.",

  "guard.block_and_explain": "Blocks",
  "guard.warn": "Warns",
  "guard.stop_and_ask": "Stop & ask",
  "guard.hold": "Hold",

  "scope.universal": "General knowledge",
  "scope.app": "App docs",
  "scope.occupation": "O*NET",
  "scope.company": "Company rule",
  "scope.personal_judgment": "Personal judgment",

  "inbox.title": "Requests from learners",
  "inbox.sub": "Each one is a real person stuck on a real screen. One tap to accept; capture starts with their moment.",
  "inbox.accept": "Accept & capture",
  "inbox.accepted": "Accepted",
  "inbox.later": "Schedule later",
  "inbox.asked": "{name} asked {ago}",
  "inbox.moment": "Their screen when they got stuck",

  "time.justNow": "just now",
  "time.min": "{n} min ago",
  "time.hour": "{n} h ago",
  "time.day": "{n} d ago",
};

export type DictKey = keyof typeof en;
type Dict = Partial<Record<DictKey, string>>;

const ru: Dict = {
  "app.tagline": "Ученик, который понимает «почему».",
  "nav.home": "Главная",
  "nav.learn": "Учиться",
  "nav.inbox": "Входящие",
  "nav.map": "Карта работы",
  "nav.skip": "К содержимому",
  "role.label": "Я",
  "role.learner": "Учусь",
  "role.expert": "Эксперт",
  "data.demo": "Демо-данные",
  "data.demo.hint": "Сервер Claros недоступен — показаны демо-данные",
  "data.live": "Онлайн",
  "lang.label": "Язык",

  "cov.ready": "Готово",
  "cov.partial": "Частично",
  "cov.missing": "Нет",
  "cov.ready.long": "Подтверждено экспертами",
  "cov.partial.long": "Часть ещё не подтверждена",
  "cov.missing.long": "Этому Claros ещё никто не показывал",

  "state.loading": "Загрузка…",
  "state.error.title": "Что-то пошло не так",
  "state.error.retry": "Повторить",
  "state.empty.workflows": "Пока нет процессов. Первый появится после записи эксперта.",
  "state.empty.requests": "Открытых запросов нет. Когда новичок застрянет на том, чего Claros не знает, запрос придёт сюда.",
  "state.empty.contrib": "Вы ещё ничему не научили Claros.",

  "home.learner.title": "Застряли на экране?",
  "home.learner.sub": "Покажите окно, в котором работаете. Claros найдёт процесс и проведёт вас по нему на вашем же случае.",
  "home.learner.cta": "Проведи меня",
  "home.learner.hint": "или скажите «Claros, проведи меня»",
  "home.learner.recent": "Процессы, которым можно научиться",
  "home.expert.title": "Ваши знания — там, где они нужны",
  "home.expert.sub": "Об этом просили новички. Claros тратит ваше время только там, где кто-то действительно застрял.",
  "home.expert.teach": "Научить Claros",
  "home.expert.inbox": "Запросы от новичков",
  "home.expert.contrib": "Процессы с вашим участием",
  "home.expert.all": "Все входящие",
  "home.experts": "экспертов",
  "home.openUnknowns": "открытых вопросов",
  "home.conflicts": "расхождений",

  "learn.invoke.title": "Проведи меня",
  "learn.invoke.sub": "Claros смотрит на окно, которым вы поделились, и слушает, что вы хотите сделать. Ничего не записывается, пока вы не начнёте.",
  "learn.invoke.share": "Показать окно",
  "learn.invoke.voice": "Говорить с Claros",
  "learn.invoke.intent": "Что вы хотите сделать?",
  "learn.invoke.intent.ph": "напр. провести этот счёт поставщика",
  "learn.invoke.demo": "Демо: покрытие",
  "learn.sharing": "Показ",
  "learn.lookup": "Ищу этот процесс…",
  "learn.lookup.sub": "Сравниваю ваш экран с библиотекой карт и O*NET",
  "learn.ready.title": "Это я знаю.",
  "learn.ready.sub": "Подтверждено: {experts}. Сделаем на вашем счёте — сначала предскажите, потом я подскажу.",
  "learn.partial.title": "Это я знаю частично.",
  "learn.partial.sub": "Научу тому, что подтвердили эксперты. Где подтверждения нет — так и скажу, а не буду гадать.",
  "learn.missing.title": "Мне это ещё никто не показывал.",
  "learn.missing.sub": "Я не буду угадывать правила вашей компании. Могу спросить эксперта — он увидит ровно тот экран, на котором вы сейчас.",
  "learn.missing.ask": "Спросить эксперта",
  "learn.missing.asked": "Запрос отправлен",
  "learn.missing.asked.sub": "{name} увидит ваш момент на экране. Вы получите уведомление, когда карта будет готова.",
  "learn.missing.onet": "Похоже на задачу",
  "learn.start": "Начать",
  "learn.step": "Шаг {n} из {total}",
  "learn.predict.q": "Что бы вы сделали здесь?",
  "learn.predict.voice": "Ответить голосом",
  "learn.predict.reveal": "Покажи",
  "learn.predict.correct": "Именно так делает эксперт.",
  "learn.predict.wrong": "Не совсем — вот почему.",
  "learn.notConfirmed": "Ещё не подтверждено",
  "learn.notConfirmed.sub": "Этот шаг не подтвердил ни один эксперт. Спросите руководителя, прежде чем действовать.",
  "learn.intervene.title": "{name} остановилась бы здесь.",
  "learn.intervene.ask": "Как думаете, почему?",
  "learn.intervene.ack": "Понятно — дальше",
  "learn.intervene.flip": "Что видел(а) {name}",
  "learn.next": "Следующий шаг",
  "learn.mastery.title": "Ваш проход",
  "learn.mastery.sub": "«Сам» — вы предсказали верно без подсказки.",
  "learn.mastery.practice": "Потренироваться",
  "learn.mastery.practice.sub": "Самый слабый шаг — на новом случае",
  "learn.lvl.unseen": "Не было",
  "learn.lvl.caught": "Поймали",
  "learn.lvl.hinted": "С подсказкой",
  "learn.lvl.unaided": "Сам",
  "learn.conflict.title": "Эксперты расходятся",
  "learn.conflict.ask": "Спросите руководителя, прежде чем выбрать.",
  "learn.end": "Завершить",

  "quote.original": "Оригинал",
  "quote.play": "Послушать эксперта",
  "quote.translation": "Перевод",

  "capture.preflight.title": "Прежде чем Claros начнёт смотреть",
  "capture.preflight.sub": "Просто работайте и рассказывайте. Claros задаст максимум несколько вопросов и только в паузах.",
  "capture.preflight.mic": "Микрофон",
  "capture.preflight.mic.ok": "Микрофон готов",
  "capture.preflight.mic.test": "Проверить микрофон",
  "capture.preflight.mic.denied": "Микрофон заблокирован — разрешите доступ в адресной строке браузера.",
  "capture.voice": "Голос",
  "debrief.stop": "Стоп",
  "capture.preflight.window": "Покажите одно окно, а не весь экран",
  "capture.preflight.window.sub": "Выберите окно приложения, в котором работаете. Другие окна, уведомления и вкладки остаются приватными.",
  "capture.preflight.captured": "Записывается",
  "capture.preflight.captured.list": "Кадры показанного окна (с маскировкой)|Что вы говорите под запись|Изменения полей, которые Claros видит на экране",
  "capture.preflight.never": "Никогда не записывается",
  "capture.preflight.never.list": "Другие окна и вкладки|Всё сказанное не под запись|Видео целиком и ваше лицо|Пароли и скрытые поля",
  "capture.preflight.lang": "Я буду говорить на",
  "capture.preflight.consent": "Я понимаю, что записывается, и согласен(на) начать.",
  "capture.preflight.start": "Начать запись",
  "capture.notebook": "Тетрадь ученика",
  "capture.events": "Что увидел Claros",
  "capture.asked": "Спросил вас",
  "capture.saved": "Отложено на разбор",
  "capture.saved.sub": "Claros придержал эти вопросы, чтобы вы могли работать.",
  "capture.budget": "Лимит вопросов",
  "capture.budget.sub": "{used} из {max} за 10 мин",
  "capture.why": "Почему Claros спросил",
  "capture.why.scope": "Тип знания",
  "capture.why.hypothesis": "Гипотеза",
  "capture.why.priority": "Приоритет",
  "capture.why.gate": "Фильтр",
  "capture.why.gate.pause": "Пауза {s} с — удобный момент",
  "capture.why.gate.resolved": "Ответ найден: {source} — не спрашивал",
  "capture.why.gate.deferred": "Отложено: вы были заняты",
  "capture.why.none": "Выберите вопрос, чтобы увидеть, почему Claros его задал.",
  "capture.offrecord": "Не под запись",
  "capture.offrecord.on": "Не под запись — ничего не сохраняется",
  "capture.offrecord.resume": "Снова под запись",
  "capture.orb": "Вынести Claros",
  "capture.orb.sub": "Плавающий шар поверх вашей работы",
  "capture.end": "Завершить и разобрать",
  "capture.live": "Идёт запись",
  "capture.empty.events": "Работайте как обычно. События экрана появятся здесь, как только Claros их заметит.",

  "debrief.title": "Разбор",
  "debrief.ledger": "Открытые вопросы",
  "debrief.ledger.done": "Вопросов больше нет. Спасибо.",
  "debrief.moment": "Этот момент",
  "debrief.question": "Claros спрашивает",
  "debrief.answer.voice": "Ответить голосом",
  "debrief.answer.type": "Написать ответ",
  "debrief.answer.skip": "Неважно",
  "debrief.answer.submit": "Сохранить ответ",
  "debrief.map": "Карта собирается",
  "debrief.teachback": "Пересказ",
  "debrief.teachback.sub": "Claros пересказывает процесс. Остановите его, как только он ошибётся.",
  "debrief.teachback.play": "Запустить пересказ",
  "debrief.correction": "Ваши исправления",
  "debrief.exam": "Экзамен: новые случаи",
  "debrief.exam.sub": "Claros предсказывает, что сделали бы вы. Отметьте каждый.",
  "debrief.exam.predicts": "Claros сделал бы",
  "debrief.exam.confidence": "уверенность",
  "debrief.exam.right": "Верно",
  "debrief.exam.wrong": "Неверно",
  "debrief.publish": "Опубликовать карту",
  "debrief.publish.sub": "Новички будут учиться ровно тому, что вы подтвердили.",
  "debrief.publish.blocked": "Сначала отметьте все случаи",
  "debrief.published": "Опубликовано",

  "map.coverage": "Покрытие",
  "map.evidence": "Шаги с доказательствами",
  "map.judgments": "Решения объяснены",
  "map.guardrails": "Ограничения подтверждены",
  "map.lane.steps": "Шаги",
  "map.lane.judgment": "Решения",
  "map.lane.guardrails": "Ограничения",
  "map.filmstrip": "Плёнка",
  "map.screen": "Момент на экране",
  "map.decision": "Решение",
  "map.reason": "Причина",
  "map.context": "Контекст",
  "map.counterfactual": "Изменится, если",
  "map.variants": "Варианты",
  "map.conflict": "Эксперты расходятся",
  "map.approve": "Утверждено",
  "map.export": "Экспорт",
  "map.export.skill": "SKILL.md",
  "map.export.mcp": "Скопировать MCP URL",
  "map.export.json": "JSON",
  "map.copied": "Скопировано",
  "map.onet": "O*NET",
  "map.version": "в{n}",
  "map.owner": "Спросить",
  "map.select": "Выберите шаг, чтобы увидеть доказательства",
  "map.unconfirmed": "Ещё не подтверждено",
  "map.notFound": "Такой карты пока нет.",

  "guard.block_and_explain": "Блокирует",
  "guard.warn": "Предупреждает",
  "guard.stop_and_ask": "Стоп и спросить",
  "guard.hold": "Удержать",

  "scope.universal": "Общее знание",
  "scope.app": "Документация",
  "scope.occupation": "O*NET",
  "scope.company": "Правило компании",
  "scope.personal_judgment": "Личное суждение",

  "inbox.title": "Запросы от новичков",
  "inbox.sub": "За каждым — живой человек, застрявший на реальном экране. Одно касание — и запись начнётся с его момента.",
  "inbox.accept": "Принять и записать",
  "inbox.accepted": "Принято",
  "inbox.later": "Запланировать",
  "inbox.asked": "{name} спросил(а) {ago}",
  "inbox.moment": "Их экран в момент затруднения",

  "time.justNow": "только что",
  "time.min": "{n} мин назад",
  "time.hour": "{n} ч назад",
  "time.day": "{n} дн назад",
};

const de: Dict = {
  "nav.home": "Start", "nav.learn": "Lernen", "nav.inbox": "Eingang", "nav.map": "Arbeitskarte",
  "role.label": "Ich bin", "role.learner": "Lernende:r", "role.expert": "Expert:in", "data.demo": "Demodaten", "lang.label": "Sprache",
  "cov.ready": "Bereit", "cov.partial": "Teilweise", "cov.missing": "Fehlt",
  "home.learner.cta": "Zeig mir, wie das geht", "home.expert.teach": "Claros etwas beibringen",
  "learn.missing.title": "Das hat mir noch niemand gezeigt.", "learn.missing.ask": "Eine Expertin fragen",
  "learn.intervene.title": "{name} würde hier anhalten.", "learn.intervene.ask": "Was glaubst du, warum?",
  "learn.notConfirmed": "Noch nicht bestätigt", "map.conflict": "Hier sind sich die Expert:innen uneinig",
  "capture.offrecord": "Inoffiziell", "debrief.publish": "Karte veröffentlichen", "inbox.accept": "Annehmen & aufnehmen",
};
const fr: Dict = {
  "nav.home": "Accueil", "nav.learn": "Apprendre", "nav.inbox": "Demandes", "nav.map": "Carte du travail",
  "role.label": "Je suis", "role.learner": "Apprenant·e", "role.expert": "Expert·e", "data.demo": "Données démo", "lang.label": "Langue",
  "cov.ready": "Prêt", "cov.partial": "Partiel", "cov.missing": "Manquant",
  "home.learner.cta": "Guide-moi", "home.expert.teach": "Apprendre quelque chose à Claros",
  "learn.missing.title": "Personne ne m'a encore montré ça.", "learn.missing.ask": "Demander à un expert",
  "learn.notConfirmed": "Pas encore confirmé", "map.conflict": "Les experts divergent ici",
  "capture.offrecord": "Hors enregistrement", "debrief.publish": "Publier la carte", "inbox.accept": "Accepter et capturer",
};
const es: Dict = {
  "nav.home": "Inicio", "nav.learn": "Aprender", "nav.inbox": "Solicitudes", "nav.map": "Mapa de trabajo",
  "role.label": "Soy", "role.learner": "Aprendiz", "role.expert": "Experto/a", "data.demo": "Datos demo", "lang.label": "Idioma",
  "cov.ready": "Listo", "cov.partial": "Parcial", "cov.missing": "Falta",
  "home.learner.cta": "Guíame con esto", "home.expert.teach": "Enseñar algo a Claros",
  "learn.missing.title": "Nadie me ha enseñado esto todavía.", "learn.missing.ask": "Preguntar a un experto",
  "learn.notConfirmed": "Aún no confirmado", "map.conflict": "Los expertos difieren aquí",
  "capture.offrecord": "Fuera de registro", "debrief.publish": "Publicar mapa", "inbox.accept": "Aceptar y capturar",
};

const DICTS: Record<UiLang, Dict> = { en, de, fr, es, ru };

export type Role = "learner" | "expert";
type Ctx = {
  lang: UiLang;
  setLang: (l: UiLang) => void;
  role: Role;
  setRole: (r: Role) => void;
  t: (k: DictKey, vars?: Record<string, string | number>) => string;
};

const UiCtx = createContext<Ctx | null>(null);

function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = window.localStorage.getItem(key);
    return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, v: string) {
  try {
    window.localStorage.setItem(key, v);
  } catch {
    /* storage blocked: preference lives for this page only */
  }
}

export function translate(lang: UiLang, k: DictKey, vars?: Record<string, string | number>) {
  let s = DICTS[lang][k] ?? en[k] ?? k;
  if (vars) for (const [name, v] of Object.entries(vars)) s = s.replaceAll(`{${name}}`, String(v));
  return s;
}

// Tiny external store so prefs survive client navigation without flicker and hydrate safely.
const listeners = new Set<() => void>();
const prefs: { lang: UiLang; role: Role; loaded: boolean } = { lang: "en", role: "learner", loaded: false };
function loadPrefs() {
  if (prefs.loaded || typeof window === "undefined") return;
  prefs.loaded = true;
  prefs.lang = read("claros.lang", LANGS, "en");
  prefs.role = read("claros.role", ["learner", "expert"] as const, "learner");
}
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const emit = () => listeners.forEach((l) => l());
const getLang = () => (loadPrefs(), prefs.lang);
const getRole = () => (loadPrefs(), prefs.role);

export function UiProvider({ children }: { children: React.ReactNode }) {
  const lang = useSyncExternalStore(subscribe, getLang, () => "en" as UiLang);
  const role = useSyncExternalStore(subscribe, getRole, () => "learner" as Role);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((l: UiLang) => {
    prefs.lang = l;
    write("claros.lang", l);
    emit();
  }, []);
  const setRole = useCallback((r: Role) => {
    prefs.role = r;
    write("claros.role", r);
    emit();
  }, []);
  const t = useCallback((k: DictKey, vars?: Record<string, string | number>) => translate(lang, k, vars), [lang]);

  const value = useMemo(() => ({ lang, setLang, role, setRole, t }), [lang, setLang, role, setRole, t]);
  return <UiCtx.Provider value={value}>{children}</UiCtx.Provider>;
}

export function useUi() {
  const c = useContext(UiCtx);
  if (!c) throw new Error("useUi must be used inside <UiProvider>");
  return c;
}
export const useT = () => useUi().t;

export function timeAgo(t: (k: DictKey, v?: Record<string, string | number>) => string, ts: number) {
  const d = Math.max(0, Date.now() - ts) / 60_000;
  if (d < 1) return t("time.justNow");
  if (d < 60) return t("time.min", { n: Math.round(d) });
  if (d < 60 * 24) return t("time.hour", { n: Math.round(d / 60) });
  return t("time.day", { n: Math.round(d / 1440) });
}
