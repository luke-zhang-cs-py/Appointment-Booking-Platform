"""
email_render.py
----------------
How an Almanac email looks, and how it words a date.

Split out of notifications.py, which had grown to five hundred lines by doing
three jobs: deciding what to send, deciding when to send it, and deciding how
it should look. This is the third one, and it is the one two other modules
need.

coffee_notifications.py used to reach into notifications.py for six
underscore-prefixed helpers -- ``_render``, ``_details``, ``_url`` and the
date formatters. That worked, and it was still one module reading another's
privates: the import was load-bearing but the underscore said "do not depend
on this". Those helpers were never private in spirit, only in name. Here they
are public, in a module whose whole purpose is to be shared, and the two
notification modules are peers importing from it rather than one burrowing
into the other.

Nothing in here touches the database or sends anything. Give it strings, get
strings back.
"""

import datetime as dt

from flask import current_app

BRAND = "Almanac"
FOOTER = "You're receiving this because you have an Almanac account."


def url(path):
    """An absolute link into the app, for a message read outside it."""
    return current_app.config["APP_BASE_URL"] + path


def escape(value):
    """Enough escaping for text placed in element bodies and quoted attributes.

    Guest-supplied names and messages reach the HTML half of an invite email,
    so this is not decorative. Single quotes are left alone deliberately:
    every attribute this module writes is double-quoted.
    """
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Dates, worded for a person
# ---------------------------------------------------------------------------
def when(appt):
    """'Mon 8 Sep, 14:30' -- short enough for a subject line."""
    try:
        date = dt.datetime.strptime(appt["date"], "%Y-%m-%d")
    except (ValueError, KeyError, TypeError):
        return f"{appt.get('date', '')} {appt.get('start_time', '')}".strip()
    return f"{date.strftime('%a')} {date.day} {date.strftime('%b')}, {appt['start_time']}"


def long_date(date_str):
    """'Monday, 8 September 2026' -- for the body of the message.

    Built from parts rather than strftime("%A, %-d %B %Y"): the %-d that
    strips a leading zero is a glibc extension, and Windows raises
    ValueError: Invalid format string for it. Same reasoning as
    coffee_chats._day_label.
    """
    try:
        date = dt.datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str
    return f"{date.strftime('%A')}, {date.day} {date.strftime('%B %Y')}"


def lead_time(appt, now):
    """'tomorrow at 14:30' / 'in about 3 hours' / 'today at 14:30'.

    A reminder that says "on 2026-09-08" makes the reader do the arithmetic.
    """
    try:
        start = dt.datetime.strptime(f"{appt['date']} {appt['start_time']}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return f"coming up on {appt['date']} at {appt['start_time']}"

    days = (start.date() - now.date()).days
    if days == 0:
        hours = max(1, round((start - now).total_seconds() / 3600))
        if hours <= 4:
            return (f"in about {hours} hour{'s' if hours != 1 else ''} "
                    f"(today at {appt['start_time']})")
        return f"today at {appt['start_time']}"
    if days == 1:
        return f"tomorrow at {appt['start_time']}"
    return f"in {days} days, on {long_date(appt['date'])} at {appt['start_time']}"


def details(appt, counterpart, status=None):
    """The (label, value) block describing one appointment."""
    rows = [
        counterpart,
        ("Date", long_date(appt["date"])),
        ("Time", f"{appt['start_time']} – {appt['end_time']}"),
    ]
    if appt.get("provider_specialty") and counterpart[0] == "Provider":
        rows.insert(1, ("Service", appt["provider_specialty"]))
    if appt.get("notes"):
        rows.append(("Notes", appt["notes"]))
    rows.append(("Status", status or appt.get("status", "confirmed")))
    return rows


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
# One call renders both halves of the multipart message, so the plain-text
# version cannot drift out of sync with the HTML one -- the failure mode where
# a wording change lands in the pretty version and the accessible version goes
# on saying something else for a year.
#
# Styles are inline because mail clients strip <style> blocks.
def render(title, intro, details, action=None, outro=""):
    """Returns (text, html) for a message. `details` is a list of (label, value)."""
    intro_lines = [line for line in intro if line]
    return (_text(title, intro_lines, details, action, outro),
            _html(title, intro_lines, details, action, outro))


def _text(title, intro_lines, details, action, outro):
    parts = [title.upper(), "=" * len(title), ""]
    parts += intro_lines + [""]
    width = max((len(label) for label, _ in details), default=0)
    parts += [f"  {label.ljust(width)}   {value}" for label, value in details]
    if action:
        parts += ["", f"{action[0]}: {action[1]}"]
    if outro:
        parts += ["", outro]
    parts += ["", f"-- {BRAND}", FOOTER]
    return "\n".join(parts)


def _detail_rows(details):
    return "".join(
        f'<tr>'
        f'<td style="padding:6px 16px 6px 0;color:#8d7a52;font-size:12px;'
        f'text-transform:uppercase;letter-spacing:0.06em;white-space:nowrap;'
        f'vertical-align:top;">{escape(label)}</td>'
        f'<td style="padding:6px 0;color:#12161d;font-size:15px;'
        f'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{escape(value)}</td>'
        f'</tr>'
        for label, value in details
    )


def _html(title, intro_lines, details, action, outro):
    intro_html = "".join(
        f'<p style="margin:0 0 12px;color:#3c4453;font-size:15px;line-height:1.55;">'
        f"{escape(line)}</p>"
        for line in intro_lines
    )
    action_html = (
        f'<p style="margin:28px 0 0;">'
        f'<a href="{escape(action[1])}" style="display:inline-block;background:#d9a441;'
        f'color:#201505;text-decoration:none;font-weight:600;font-size:14px;'
        f'padding:11px 20px;border-radius:6px;">{escape(action[0])}</a></p>'
        if action
        else ""
    )
    outro_html = (
        f'<p style="margin:24px 0 0;color:#6b7280;font-size:13px;line-height:1.5;">'
        f"{escape(outro)}</p>"
        if outro
        else ""
    )

    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#ece6d6;
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
         style="max-width:560px;margin:0 auto;background:#fffdf7;border-radius:10px;
                border:1px solid #ded5bf;overflow:hidden;">
    <tr><td style="height:4px;background:#d9a441;"></td></tr>
    <tr><td style="padding:28px 32px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:0.18em;
                text-transform:uppercase;color:#8d7a52;">{BRAND}</p>
      <h1 style="margin:0 0 18px;font-size:21px;color:#12161d;font-weight:650;"
          >{escape(title)}</h1>
      {intro_html}
      <table role="presentation" cellpadding="0" cellspacing="0"
             style="margin:20px 0 0;border-top:1px solid #ece6d6;
                    border-bottom:1px solid #ece6d6;padding:8px 0;width:100%;">
        {_detail_rows(details)}
      </table>
      {action_html}
      {outro_html}
    </td></tr>
    <tr><td style="padding:16px 32px;background:#f6f1e4;color:#8d7a52;font-size:12px;
                   border-top:1px solid #ece6d6;">
      {FOOTER}
    </td></tr>
  </table>
</body></html>"""
