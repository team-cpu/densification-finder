import pytest
from email_templates import notification_html


def test_template_escapes_contact_content_and_includes_cta():
    html = notification_html('Scope: <script>bad</script>', 'Müller & Co\n<svg onload=x>\n\nBoard öffnen: https://scope.example', 'https://scope.example')
    assert '<script>' not in html and '<svg onload' not in html
    assert '&lt;svg onload=x&gt;' in html and 'Müller &amp; Co' in html
    assert 'href="https://scope.example"' in html
    assert 'max-width:600px' in html and 'width=device-width' in html


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://scope.example', 'https://user:pass@example.com'])
def test_template_rejects_unsafe_link(url):
    with pytest.raises(ValueError):
        notification_html('Scope', 'Body', url)
