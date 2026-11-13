# SPDX-License-Identifier: GPL-3.0-or-later
"""The words of the interface for the activities: field labels, states, errors, times."""

from dataclasses import dataclass
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

# the displayed state of an activity (model.display, ACTIVITY-MODEL.md 2.2)
DISPLAY_STATES = {
    model.DELETED: _("Eliminata"), ABSENCE: _("Assenza"), model.LATE: _("In ritardo"), model.PLANNED: _("Pianificata"),
    TODO: _("Da fare"), WORK: _("In lavoro"), TRAVEL: _("In viaggio"), PAUSE: _("In pausa"),
    model.SUSPENDED: _("Sospesa"), model.DONE: _("Chiusa"), model.INCOMPLETE: _("Dettagli mancanti"),
}
# the symbol and the colour of each displayed state on a terminal (§2.2: icon and colour); the colour
# names of terminal.COLOURS of the cli and the tui: orange, brown and pink need 256 colours, else the
# nearest of the eight
DISPLAY_SYMBOLS = {
    model.DELETED: "✗", ABSENCE: "●", model.LATE: "!", model.PLANNED: "○", TODO: "☐", WORK: "▶", TRAVEL: "➜",
    PAUSE: "‖", model.SUSPENDED: "☾", model.DONE: "✓", model.INCOMPLETE: "■",
}
DISPLAY_COLOURS = {
    model.DELETED: "grey", ABSENCE: "pink", model.LATE: "brown", model.PLANNED: "grey", TODO: "white", WORK: "green",
    TRAVEL: "blue", PAUSE: "yellow", model.SUSPENDED: "orange", model.DONE: "greenish", model.INCOMPLETE: "reddish",
}
# the stored states, as the owner names them (2.1)
STATUSES = {TODO: _("Da fare"), RUNNING: _("In corso"), CLOSED: _("Chiusa")}
PRIORITIES = {0: _("Nessuna"), 1: _("Bassa"), 2: _("Media"), 3: _("Alta"), 4: _("Urgente"), 5: _("Emergenza")}
ABSENCE_KINDS = {ABSENCE: _("Assenza"), "HOLIDAY": _("Ferie"), "LEAVE": _("Permesso"), "SICK": _("Malattia")}
# what a command leaves to tell the user (Work.notices), and what a sync does (Store.notices: %s is the
# activity's title, as typed)
NOTICES = {
    "end_moved": _("La fine di «%s» è stata anticipata all'inizio della nuova attività."),
    # a segment wholly ahead the new start came before: a TRAVEL one (a declared return trip), another
    "return_removed": _("Il viaggio di ritorno previsto è stato rimosso."),
    "segment_removed": _("Un tratto previsto è stato rimosso."),
    "edit_dropped": _("La modifica a «%s» non è stata applicata: il da fare è stato chiuso o avviato su un "
                      "altro dispositivo."),
}


def notice_text(code, title=""):
    """The words of a notice (NOTICES); [title] fills the %s of those that name a record."""
    # translated when said: the language may have been chosen after this module was imported
    text = _(NOTICES.get(code, code))
    return text % title if "%s" in text else text


def sync_notices(session):
    """The words of what the sync left to tell the user (Store.notices), oldest first; the interface
    dismisses them with session.store.dismiss_notices() once shown, else they go after a week."""
    return [notice_text(n.get("code"), n.get("title") or "") for n in session.store.notices()]


# the Home list (app HomeList.kt, HomeTabs.kt)
SECTIONS = {
    "overdue": _("In ritardo"), "missing": _("Dettagli mancanti"), "today": _("Oggi"),
    "next": _("Prossimi giorni"), "earlier": _("Giorni precedenti"), "no_date": _("Senza data"),
    "ignored": _("Cestino"),
}
WINDOWS = {-1: _("Oggi"), 24: _("24 h"), 72: _("3 gg"), 168: _("7 gg"), 720: _("30 gg"), 0: _("Tutti")}
SHOWS = {"TODO": _("Da fare"), "MISSING": _("Dettagli mancanti"), "DONE": _("Fatte"), "IGNORED": _("Cestino")}

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
}


def field_label(policy, name):
    return policy.field(name).label or FIELDS[name]


def error_text(e):
    if e.code == "required":
        return _("Campo obbligatorio: %s.") % FIELDS.get(e.detail, e.detail)
    text = ERRORS.get(e.code, e.code)
    return text % e.detail if "%s" in text else text


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
    from .model import day_of as _day, plan_bounds
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
        text = _("Sincronizzato alle %s") % formats.time(datetime.fromtimestamp(last / 1000))
    else:
        text = _("In attesa della prima sincronizzazione…")
    # a change the sync could not apply (issue #21): said where every interface shows the sync's state
    return " ".join([text] + sync_notices(session))


def sync_needs_attention(session, state="idle"):
    return bool(session.account is None or session.store.get_state("revoked")
                or session.store.get_state("incompatible") or state == "failed"
                or session.store.notices())


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


@dataclass
class Action:
    """A button of an activity: [verb] is a Work command (Work.perform), or "travel_from_work" / "return_trip"."""
    label: str
    verb: str
    kind: str | None = None


def entry_actions(work, e):
    """
    [Action] the state of the activity [e] allows, as the app's detail dialog (§3, §8): no "Sospendi"
    (suspend has no button), and from a pause "Riprendi" is the kind open before it. The stop of an
    activity that asks the return trip (Work.return_trip_offered) and the travel from work (Work.
    asks_completion) are for the interface to put as questions.
    """
    policy = work.policy
    if e.get("deletedUtcMs") is not None:
        return [Action(_("Ripristina"), "restore")]
    if e.get("kind") == ABSENCE:
        return [Action(_("Elimina"), "delete")] if policy.feature("deleteEntries") else []
    travel = policy.feature("travel")
    status = e.get("status")
    out = []
    if status == TODO:
        out.append(Action(_("Lavoro"), "start", WORK))
        if travel:
            out.append(Action(_("Viaggio"), "start", TRAVEL))
        out.append(Action(_("Fatto"), "tick"))
        if policy.feature("deleteTodos") or policy.feature("ignoreTodos"):
            out.append(Action(_("Elimina"), "delete"))
        return out
    if status == RUNNING:
        seg = work.open_segment(e["uid"])
        mode = seg.get("kind") if seg is not None else None
        if mode is None:
            out.append(Action(_("Lavoro"), "resume", WORK))
            if travel:
                out.append(Action(_("Viaggio"), "resume", TRAVEL))
        elif mode == WORK:
            out.append(Action(_("Pausa"), "switch", PAUSE))
            if travel:
                out.append(Action(_("Viaggio"), "travel_from_work", TRAVEL))
        elif mode == TRAVEL:
            out.append(Action(_("Lavoro"), "switch", WORK))
            out.append(Action(_("Pausa"), "switch", PAUSE))
        else:
            before = work.resume_kind(e["uid"])
            out.append(Action(_("Riprendi"), "switch", before))
            other = TRAVEL if before == WORK else WORK
            if other == WORK or travel:
                out.append(Action(KINDS[other], "switch", other))
        out.append(Action(_("Stop"), "stop"))
    elif status == CLOSED:
        if not work.real_segments(e["uid"]):
            out.append(Action(_("Da fare"), "untick"))
        out.append(Action(_("Riapri"), "reopen", WORK))
    if policy.feature("deleteEntries"):
        out.append(Action(_("Elimina"), "delete"))
    return out
