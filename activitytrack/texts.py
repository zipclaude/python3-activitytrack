# SPDX-License-Identifier: GPL-3.0-or-later
"""The words of the interface for the activities: field labels, states, errors, times."""

from datetime import datetime

from . import formats
from .i18n import _
from .work import ACTIVE, CLOSED, PAUSE, SUSPENDED, TO_COMPLETE, TRAVEL, WORK

# the app's names of the fields; the policy may rename them
FIELDS = {
    "company": _("Azienda"), "client": _("Cliente"), "role": _("Ruolo"), "quickPick": _("Scelta rapida"),
    "description": _("Descrizione"), "locality": _("Località"), "notes": _("Note"), "tags": _("Tag"),
    "times": _("Tempi"), "todoPriority": _("Priorità"), "todoDue": _("Scadenza"), "todoPlan": _("Pianificazione"),
}

MODES = {WORK: _("In lavoro"), PAUSE: _("In pausa"), TRAVEL: _("In viaggio")}
KINDS = {WORK: _("Lavoro"), PAUSE: _("Pausa"), TRAVEL: _("Viaggio")}
STATES = {SUSPENDED: _("Sospesa"), TO_COMPLETE: _("Dettagli mancanti"), CLOSED: _("Chiusa")}

# the Home list (app HomeList.kt, HomeTabs.kt)
SECTIONS = {
    "overdue": _("In ritardo"), "missing": _("Dettagli mancanti"), "today": _("Oggi"),
    "next": _("Prossimi giorni"), "earlier": _("Giorni precedenti"), "no_date": _("Senza data"),
    "ignored": _("Ignorate"),
}
WINDOWS = {-1: _("Oggi"), 24: _("24 h"), 72: _("3 gg"), 168: _("7 gg"), 720: _("30 gg"), 0: _("Tutti")}
SHOWS = {"TODO": _("Da fare"), "MISSING": _("Dettagli mancanti"), "DONE": _("Fatte"), "IGNORED": _("Ignorate")}
TODO_STATES = {"open": _("Aperta"), "planned": _("Pianificata"), "overdue": _("In ritardo"), "done": _("Fatta"),
               "ignored": _("Ignorata")}

ERRORS = {
    "client_required": _("Serve il cliente (o un viaggio)."),
    "client_company_mismatch": _("Il cliente è di un'altra azienda."),
    "master_locked": _("La policy non permette di creare anagrafiche: scegli tra quelle esistenti."),
    "future_time": _("L'orario è nel futuro."),
    "invalid_range": _("La fine è prima dell'inizio."),
    "too_short": _("L'attività è troppo breve."),
    "before_segment": _("L'orario è prima dell'ultimo cambio registrato."),
    "details_missing": _("Manca la descrizione."),
    "todos_only": _("La policy permette di avviare attività solo dai Da fare."),
    "tag_not_allowed": _("La policy permette solo i tag delle anagrafiche: «%s» non c'è."),
    "times_locked": _("La policy non permette di modificare i tempi."),
    "illegal": _("Comando non possibile in questo stato."),
    "feature": _("Funzione disattivata dalla policy."),
    "not_found": _("Non c'è più (eliminata su un altro dispositivo?)."),
    "title_required": _("Serve il titolo."),
    "plan_day_required": _("Per un orario pianificato serve il giorno."),
    "plan_time_required": _("Per una durata serve l'orario di inizio."),
    "invalid_time": _("Orario non valido."),
    "invalid_priority": _("La priorità va da 1 a 5."),
    "imported": _("Un Da fare del gestionale non si elimina: si ignora."),
}


def field_label(policy, name):
    return policy.field(name).label or FIELDS[name]


def error_text(e):
    if e.code == "required":
        return _("Campo obbligatorio: %s.") % FIELDS.get(e.detail, e.detail)
    text = ERRORS.get(e.code, e.code)
    return text % e.detail if "%s" in text else text


def state_label(entry):
    if entry.get("status") == ACTIVE:
        return MODES.get(entry.get("mode"), "")
    return STATES.get(entry.get("status"), "")


def hm(ms):
    """Hours and minutes, as the app shows durations: 1:05."""
    minutes = max(0, ms) // 60_000
    return f"{minutes // 60}:{minutes % 60:02d}"


def clock(ms):
    """The local time of day of an instant, with the date when not today."""
    t = datetime.fromtimestamp(ms / 1000)
    if t.date() == datetime.now().date():
        return formats.time(t)
    return f"{formats.day(t.date(), year=False)} {formats.time(t)}"


def todo_when(t, tz):
    """When a to-do is planned or due, as the app's rows: "lun 02/11 15:00–15:30", "scad. gio 05/11" (formats)."""
    from .todos import plan_bounds, _day
    days = (_("lun"), _("mar"), _("mer"), _("gio"), _("ven"), _("sab"), _("dom"))
    plan = plan_bounds(t, tz)
    today = datetime.now(tz).date()
    if plan:
        start, end, all_day = plan
        d = start.date()
        day = "" if d == today else f"{days[d.weekday()]} {formats.day(d, year=False)}"
        if all_day:
            return day or _("oggi")
        span = formats.time(start) + (("–" + formats.time(end)) if end > start else "")
        return f"{day} {span}".strip()
    due = _day(t.get("dueDate"))
    if due:
        text = _("scad. %s") % f"{days[due.weekday()]} {formats.day(due, year=False)}"
        if t.get("dueTimeMin") is not None:
            text += " " + formats.minutes(t["dueTimeMin"])
        return text
    return ""


# ---- pairing and sync, for every interface ----

def pair_error(code):
    """What a refused pairing says (pairing.PairError.code)."""
    from . import pairing
    errors = {
        pairing.NOT_A_LINK: _("Non è un collegamento di associazione: copialo dalla pagina «Dispositivi» del web."),
        pairing.VERSION: _("Collegamento di una versione che questa app non conosce: aggiorna ActivityTrack sul PC."),
        pairing.NOT_HTTPS: _("Il server deve usare HTTPS."),
        pairing.BAD_PIN: _("Il collegamento è incompleto: copialo di nuovo."),
        pairing.INVALID_CODE: _("Codice scaduto o già usato: creane un altro dalla pagina web."),
        pairing.USER_DISABLED: _("Il tuo utente non può sincronizzare: chiedi all'amministratore."),
        pairing.NETWORK: _("Server non raggiungibile: controlla la rete e l'indirizzo."),
        pairing.CERTIFICATE: _("Il certificato del server non è valido o non corrisponde al collegamento."),
        pairing.SERVER: _("Il server ha risposto con un errore."),
        pairing.APP_TOO_OLD: _("Il server non accetta questa versione: aggiorna ActivityTrack sul PC."),
        pairing.SERVER_TOO_OLD: _("Questa versione è più recente del server: va aggiornato il server."),
    }
    return errors.get(code, code)


def sync_state(session, state="idle", detail=""):
    """
    What the sync says: [state] is the last run's (ok, idle, offline, failed,
    revoked, incompatible, unpaired), the rest comes from the store.
    """
    from . import wire
    store = session.store
    if session.account is None:
        return _("Non associato: associa questo PC a un server ActivityTrack Web.")
    if store.get_state("revoked"):
        return _("Il server ha revocato l'associazione di questo PC. I dati restano qui: "
                 "per sincronizzare di nuovo associalo dalla pagina «Dispositivi» del web.")
    code = store.get_state("incompatible")
    if code == wire.SERVER_TOO_OLD:
        return _("Questa versione è più recente del server ActivityTrack Web: va aggiornato il server "
                 "(avvisa l'amministratore). I dati restano qui e si sincronizzano dopo l'aggiornamento.")
    if code:
        return _("Il server ActivityTrack Web non accetta più questa versione: aggiorna ActivityTrack sul PC. "
                 "I dati restano qui e si sincronizzano dopo l'aggiornamento.")
    if state == "offline":
        return _("Server non raggiungibile: le modifiche partono appena torna la rete.")
    if state == "failed":
        return _("Sincronizzazione non riuscita: %s") % detail
    last = store.get_state("last_sync")
    if last:
        return _("Sincronizzato alle %s") % formats.time(datetime.fromtimestamp(last / 1000))
    return _("In attesa della prima sincronizzazione…")


def sync_needs_attention(session, state="idle"):
    return bool(session.account is None or session.store.get_state("revoked")
                or session.store.get_state("incompatible") or state == "failed")


def entry_commands(entry, policy):
    """[(label, command)] the state allows, as the app's card buttons (every interface)."""
    from .work import STOP, SUSPEND
    status, mode = entry.get("status"), entry.get("mode")
    travel = policy.feature("travel")
    out = []
    if status == ACTIVE:
        if mode != WORK:
            out.append((_("Riprendi") if mode == PAUSE else _("Lavoro"), WORK))
        if mode != PAUSE:
            out.append((_("Pausa"), PAUSE))
        if mode != TRAVEL and travel:
            out.append((_("Viaggio"), TRAVEL))
        if mode != PAUSE:
            out.append((_("Sospendi"), SUSPEND))
        out.append((_("Stop"), STOP))
    elif status == SUSPENDED:
        out.append((_("Riprendi"), WORK))
        if travel:
            out.append((_("Viaggio"), TRAVEL))
        out.append((_("Stop"), STOP))
    return out
