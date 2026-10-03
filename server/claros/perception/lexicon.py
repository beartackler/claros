"""Multilingual UI vocabulary (en/de/fr/es/ru) for app-agnostic cues: statuses, toasts, dialog buttons.
Generic across apps — no app-specific strings."""
from __future__ import annotations

import re
from typing import Optional

STATUS = {
    "draft": "draft entwurf brouillon borrador черновик",
    "submitted": "submitted gebucht übermittelt eingereicht validé soumis comptabilisé enviado validado "
                 "contabilizado проведен проведён отправлен",
    "approved": "approved genehmigt freigegeben approuvé aprobado утвержден утверждён одобрен согласован",
    "rejected": "rejected abgelehnt rejeté refusé rechazado отклонен отклонён",
    "on_hold": "on hold angehalten zurückgestellt en attente suspendu en espera retenido приостановлен на удержании",
    "cancelled": "cancelled canceled storniert abgebrochen annulé cancelado anulado отменен отменён аннулирован",
    "paid": "paid bezahlt payé pagado оплачен",
    "unpaid": "unpaid unbezahlt impayé impagado не оплачен",
    "overdue": "overdue überfällig en retard vencido просрочен",
    "open": "open offen ouvert abierto открыт",
    "closed": "closed geschlossen fermé cerrado закрыт",
    "not_saved": "not saved nicht gespeichert non enregistré no guardado не сохранено",
    "completed": "completed abgeschlossen terminé completado завершен завершён",
    "pending": "pending ausstehend en attente pendiente в ожидании",
}

STATUS_EVENT = {"submitted": "submit", "approved": "approve", "rejected": "reject", "on_hold": "hold"}

TOAST_SAVE = re.compile(
    r"(?i)\b(saved|gespeichert|enregistr[ée]e?s?|guardad[oa]s?|сохранен[оаы]?|сохранён)\b|"
    r"\b(changes saved|änderungen gespeichert|modifications enregistrées|cambios guardados|изменения сохранены)\b")
TOAST_SUBMIT = re.compile(
    r"(?i)\b(submitted|gebucht|übermittelt|soumis|validé|enviado|contabilizado|проведен|проведён|отправлен)\b")
TOAST_ERROR = re.compile(
    r"(?i)\b(error|fehler|erreur|не удалось|ошибка|failed|fehlgeschlagen|échec|fall[óo])\b")

BTN_OK = {"ok", "yes", "confirm", "submit", "ja", "bestätigen", "oui", "confirmer", "sí", "si", "confirmar",
          "да", "подтвердить", "continue", "weiter", "continuer", "continuar", "продолжить", "delete", "löschen",
          "supprimer", "eliminar", "удалить"}
BTN_CANCEL = {"cancel", "no", "abbrechen", "nein", "annuler", "non", "cancelar", "отмена", "нет", "close",
              "schließen", "fermer", "cerrar", "закрыть"}

_PHRASES: dict[str, list[str]] = {
    "on_hold": ["on hold", "en attente", "en espera", "на удержании"],
    "not_saved": ["not saved", "nicht gespeichert", "non enregistré", "no guardado", "не сохранено"],
    "unpaid": ["не оплачен"],
    "pending": ["в ожидании"],
}
_PHRASE_TOKENS = {w for ps in _PHRASES.values() for p in ps for w in p.split()}
_SINGLE = {k: set(v.split()) - _PHRASE_TOKENS for k, v in STATUS.items()}


def status_of(text: str) -> Optional[str]:
    """Canonical status key if `text` (a short UI line) is a status label."""
    t = text.strip().lower()
    if not t or len(t) > 32 or len(t.split()) > 3:
        return None
    for key, phrases in _PHRASES.items():
        if any(t == p for p in phrases):
            return key
    for key, words in _SINGLE.items():
        if t in words:
            return key
    return None


def is_button(text: str, kind: str) -> bool:
    t = text.strip().lower().strip(".!")
    return t in (BTN_OK if kind == "ok" else BTN_CANCEL)


ACTIONS = {"save", "speichern", "enregistrer", "guardar", "сохранить", "submit", "buchen", "valider", "enviar",
           "провести", "new", "neu", "nouveau", "nuevo", "создать", "edit", "bearbeiten", "modifier", "editar",
           "изменить", "menu", "help", "hilfe", "aide", "ayuda", "справка", "search", "suche", "rechercher",
           "buscar", "поиск", "actions", "aktionen", "acciones", "действия", "print", "drucken", "imprimer",
           "imprimir", "печать"}


def is_action(text: str) -> bool:
    return text.strip().lower().strip(".!…") in ACTIONS
