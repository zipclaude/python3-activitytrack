# SPDX-License-Identifier: GPL-3.0-or-later
"""The words of the interface for the activities: field labels, states, errors, times."""

from datetime import datetime

from . import formats
from .i18n import _
from . import model
from .model import ABSENCE, CLOSED, PAUSE, RUNNING, TODO, TRAVEL, WORK

# the app's names of the fields; the policy may rename them
FIELDS = {
    "company": _("Azienda"), "client": _("Cliente"), "role": _("Ruolo"), "quickPick": _("Scelta rapida"),
    "description": _("Descrizione"), "locality": _("Località"), "notes": _("Note"), "tags": _("Tag"),
    "times": _("Tempi"), "todoPriority": _("Priorità"), "todoDue": _("Scadenza"), "todoPlan": _("Pianificazione"),
}

MODES = {WORK: _("In lavoro"), PAUSE: _("In pausa"), TRAVEL: _("In viaggio")}
KINDS = {WORK: _("Lavoro"), PAUSE: _("Pausa"), TRAVEL: _("Viaggio"), ABSENCE: _("Assenza")}
# TODO(activity-1.0-ifaces): the statuses of the schemas before 3 (work.SUSPENDED, work.TO_COMPLETE)
STATES = {"SUSPENDED": _("Sospesa"), "TO_COMPLETE": _("Dettagli mancanti"), CLOSED: _("Chiusa")}

# the displayed state of an activity (model.display, ACTIVITY-MODEL.md 2.2)
DISPLAY_STATES = {
    model.DELETED: _("Eliminata"), ABSENCE: _("Assenza"), model.LATE: _("In ritardo"), model.PLANNED: _("Pianificata"),
    TODO: _("Da fare"), WORK: _("In lavoro"), TRAVEL: _("In viaggio"), PAUSE: _("In pausa"),
    model.SUSPENDED: _("Sospesa"), model.DONE: _("Chiusa"), model.INCOMPLETE: _("Dettagli mancanti"),
}
# the stored states, as the owner names them (2.1)
STATUSES = {TODO: _("Da fare"), RUNNING: _("In corso"), CLOSED: _("Chiusa")}
PRIORITIES = {0: _("Nessuna"), 1: _("Bassa"), 2: _("Media"), 3: _("Alta"), 4: _("Urgente"), 5: _("Emergenza")}
ABSENCE_KINDS = {ABSENCE: _("Assenza"), "HOLIDAY": _("Ferie"), "LEAVE": _("Permesso"), "SICK": _("Malattia")}
# what a command leaves to tell the user (Work.notices)
NOTICES = {"end_moved": _("La fine dell'attività precedente è stata anticipata all'inizio di questa.")}

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
    "planned_only": _("La policy permette di avviare attività solo dai Da fare."),
    "todos_only": _("La policy permette di avviare attività solo dai Da fare."),
    "no_working_days": _("Nel periodo non ci sono giorni lavorativi: indica un orario."),
    "tag_not_allowed": _("La policy permette solo i tag delle anagrafiche: «%s» non c'è."),
    "times_locked": _("La policy non permette di modificare i tempi."),
    "illegal": _("Comando non possibile in questo stato."),
    "feature": _("Funzione disattivata dalla policy."),
    "not_found": _("Non c'è più (eliminata su un altro dispositivo?)."),
    "title_required": _("Serve il titolo."),
    "plan_day_required": _("Per un orario pianificato serve il giorno."),
    "plan_time_required": _("Per una durata serve l'orario di inizio."),
    "invalid_time": _("Orario non valido."),
    "invalid_priority": _("La priorità va da 0 a 5."),
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
    """The state of an activity record, from what it stores (display_label has the full rules)."""
    if entry.get("deletedUtcMs") is not None:
        return DISPLAY_STATES[model.DELETED]
    if entry.get("kind") == ABSENCE:
        return DISPLAY_STATES[ABSENCE]
    status = entry.get("status")
    if status == RUNNING:
        return MODES.get(entry.get("mode"), DISPLAY_STATES[model.SUSPENDED])
    return STATUSES.get(status, "")


def display_label(state):
    """The words of a displayed state (model.display's first value)."""
    return DISPLAY_STATES.get(state, state)


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

# what a server of the wire schema 3 answers to an older client (ACTIVITY-MODEL.md §6)
UPGRADE_REQUIRED = _("Aggiorna l'app per sincronizzare")

def pair_error(code):
    """What a refused pairing says (pairing.PairError.code)."""
    from . import pairing
    errors = {
        pairing.UPGRADE_REQUIRED: UPGRADE_REQUIRED,
        pairing.NOT_A_LINK: _("Non è un collegamento di associazione: copialo dalla pagina «Dispositivi» del web."),
        pairing.VERSION: _("Collegamento di una versione che questa app non conosce: aggiorna ActivityTrack sul PC."),
        pairing.NOT_HTTPS: _("Il server deve usare HTTPS."),
        pairing.NOT_LOCAL: _("http:// è permesso solo per un server locale (localhost o una rete privata)."),
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
    if code == wire.UPGRADE_REQUIRED:
        return UPGRADE_REQUIRED
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


def update_state(status, version):
    """What the updates screen says of an updates.Status; [version] is the installed one."""
    from . import manifest, updates
    s = status.state
    if s == updates.OFF:
        return _("Controllo degli aggiornamenti disattivato.")
    if s == updates.UNPAIRED:
        return _("Non associato: gli aggiornamenti arrivano dal server a cui il PC è associato.")
    if s == updates.UNTRUSTED:
        if status.error == "no key":
            return _("Il collegamento di associazione non portava la chiave del server per gli aggiornamenti: "
                     "associa di nuovo il PC per riceverli.")
        return _("L'annuncio del server non è firmato con la sua chiave: ignorato.")
    if s == updates.REVOKED:
        return _("Il server ha revocato l'associazione di questo PC. I dati restano qui: "
                 "per sincronizzare di nuovo associalo dalla pagina «Dispositivi» del web.")
    if s == updates.INCOMPATIBLE:
        from . import wire
        if status.error == wire.UPGRADE_REQUIRED:
            return UPGRADE_REQUIRED
        if status.error == wire.SERVER_TOO_OLD:
            return _("Questa versione è più recente del server ActivityTrack Web: va aggiornato il server "
                     "(avvisa l'amministratore). I dati restano qui e si sincronizzano dopo l'aggiornamento.")
        return _("Il server ActivityTrack Web non accetta più questa versione: aggiorna ActivityTrack sul PC. "
                 "I dati restano qui e si sincronizzano dopo l'aggiornamento.")
    if s == updates.ERROR:
        return _("Server non raggiungibile: %s") % status.error
    if s == updates.AVAILABLE:
        text = _("È disponibile la versione %s.") % status.release.version
        if status.downloadable:
            return text
        if manifest.from_package_manager():
            return text + " " + _("Si aggiorna con il gestore di pacchetti del sistema.")
        return text + " " + _("Per questa installazione il server non ha un pacchetto.")
    return _("Nessun aggiornamento: %s è l'ultima versione.") % version


def entry_commands(entry, policy):
    """[(label, command)] the state allows, as the app's card buttons (every interface)."""
    from .work import REOPEN, STOP
    status, mode = entry.get("status"), entry.get("mode")
    if entry.get("deletedUtcMs") is not None or entry.get("kind") == ABSENCE:
        return []
    travel = policy.feature("travel")
    out = []
    if status == TODO:
        out.append((_("Lavoro"), WORK))
        if travel:
            out.append((_("Viaggio"), TRAVEL))
    elif status == RUNNING:
        if mode != WORK:
            out.append((_("Riprendi") if mode in (PAUSE, None) else _("Lavoro"), WORK))
        if mode in (WORK, TRAVEL):
            out.append((_("Pausa"), PAUSE))
        if mode != TRAVEL and travel:
            out.append((_("Viaggio"), TRAVEL))
        out.append((_("Stop"), STOP))
    elif status == CLOSED:
        out.append((_("Riapri"), REOPEN))
    return out
