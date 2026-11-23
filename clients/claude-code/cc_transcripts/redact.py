"""Secret redaction that is safe to run over raw JSONL text.

Every pattern stops at a JSON string boundary (`"` or a backslash escape), so a
redacted line is still valid JSON with the same shape. The replacement holds no
quote or backslash. Patterns are deliberately broad: a false positive costs a
few characters of archive; a false negative ships a credential.
"""

import re

MARK = "***REDACTED***"

# Characters that may appear inside a token without leaving the JSON string.
_T = r'[^\s"\\]'

# Whole-token patterns: the full match is replaced.
_TOKEN_PATTERNS = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    r"\bglc_[A-Za-z0-9+/=_-]{20,}",            # Grafana Cloud access-policy token
    r"\bglsa_[A-Za-z0-9_]{20,}",               # Grafana service-account token
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",           # GitHub classic tokens
    r"\bgithub_pat_[A-Za-z0-9_]{40,}",         # GitHub fine-grained PAT
    r"\bsk-ant-[A-Za-z0-9_-]{20,}",            # Anthropic API key
    r"\bsk-[A-Za-z0-9_-]{32,}",                # OpenAI-style keys
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",          # AWS access key id
    r"\bxox[abposr]-[A-Za-z0-9-]{10,}",        # Slack tokens
    r"\bAIza[0-9A-Za-z_-]{35}\b",              # Google API key
    r"\b0\.[A-Za-z0-9_-]{60,}\b",              # Bitwarden Secrets Manager access token
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",  # JWT
    r"(?<=://)[^\s\"\\/:@]+:[^\s\"\\/@]+(?=@)",  # user:pass@ in URLs
]

# Keyword patterns: only the value (group 1) is replaced, the keyword stays so a
# reader can see what was removed. An auth scheme word after the keyword is
# skipped, matching drosera's Alloy scrub (#325).
_KEYWORD = (
    r"(?i)(?:bearer|authorization|token|password|passwd|secret|api[_-]?key|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret)"
)
_KV_PATTERN = re.compile(
    _KEYWORD + r"(?:\\?\")?[\s:=]+(?:\\?\")?(?:(?:bearer|basic|token)\s+)?(" + _T + r"{6,})"
)

_TOKEN_RE = re.compile("|".join(f"(?:{p})" for p in _TOKEN_PATTERNS), re.DOTALL)


def redact(text):
    """Return ``(redacted_text, count)``."""
    count = 0

    def _whole(_m):
        nonlocal count
        count += 1
        return MARK

    text = _TOKEN_RE.sub(_whole, text)

    def _value(m):
        nonlocal count
        if m.group(1) == MARK or m.group(1).startswith(MARK):
            return m.group(0)
        count += 1
        start, end = m.span(1)
        base = m.start(0)
        whole = m.group(0)
        return whole[: start - base] + MARK + whole[end - base:]

    text = _KV_PATTERN.sub(_value, text)
    return text, count
