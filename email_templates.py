"""Small, escaped, table-based email layout with a plain-text alternative."""
from html import escape
from urllib.parse import urlsplit


def notification_html(subject: str, text: str, url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Für den E-Mail-Link ist eine gültige HTTPS-Adresse erforderlich.')
    sections = []
    for paragraph in text.split('\n\n'):
        if paragraph.strip() == subject.removeprefix('Scope: ').strip() or paragraph.startswith('Board öffnen:'):
            continue
        sections.append('<p style="margin:0 0 20px;line-height:1.65;overflow-wrap:anywhere">'
                        + escape(paragraph).replace('\n', '<br>') + '</p>')
    return '''<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f3f5f4;color:#243c39;font-family:Arial,Helvetica,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border:1px solid #dfe7e4;border-radius:12px">
<tr><td style="padding:24px 28px;background:#1c4e4a;color:#ffffff;font-size:24px;font-weight:bold">Scope</td></tr>
<tr><td style="padding:28px;font-size:15px"><h1 style="font-size:24px;line-height:1.3;margin:0 0 24px">''' + escape(subject.removeprefix('Scope: ')) + '</h1>' + ''.join(sections) + '''
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:#1c4e4a;border-radius:6px">
<a href="''' + escape(url, quote=True) + '''" style="display:inline-block;padding:14px 22px;color:#ffffff;text-decoration:none;font-weight:bold">Board öffnen</a>
</td></tr></table></td></tr><tr><td style="padding:20px 28px;border-top:1px solid #e6ece9;color:#647571;font-size:12px;line-height:1.6">
Scope · Immobilien-Screening<br>Sie erhalten diese Nachricht aufgrund der E-Mail-Einstellungen Ihrer Organisation.
</td></tr></table></td></tr></table></body></html>'''
