"""Real-shape ALB access-log sample lines for the unit tests.

Adapted from the AWS documented examples plus edge cases we must handle:
quoted fields containing spaces, escaped quotes inside user_agent, ``-`` empty
markers, an HTTP and an HTTPS line, a dropped/blocked request (``"- - -"``),
and a newer line carrying the trailing ``conn_trace_id`` field.
"""

# Classic HTTP request — no TLS fields, ssl_cipher/ssl_protocol are "-".
HTTP_LINE = (
    'http 2018-07-02T22:23:00.186641Z app/my-loadbalancer/50dc6c495c0c9188 '
    '192.168.131.39:2817 10.0.0.1:80 0.000 0.001 0.000 200 200 34 366 '
    '"GET http://www.example.com:80/ HTTP/1.1" "curl/7.46.0" - - '
    'arn:aws:elasticloadbalancing:us-east-2:123456789012:targetgroup/my-targets/73e2d6bc24d8a067 '
    '"Root=1-58337262-36d228ad5d99923122bbe354" "-" "-" '
    '0 2018-07-02T22:22:48.364000Z "forward" "-" "-" "10.0.0.1:80" "200" "-" "-"'
)

# HTTPS request — carries ssl_cipher/ssl_protocol, a real domain_name (Host)
# and a chosen_cert_arn. This is the visitor-source signal we archive.
HTTPS_LINE = (
    'https 2018-07-02T22:23:00.186641Z app/my-loadbalancer/50dc6c495c0c9188 '
    '203.0.113.12:41940 10.0.0.1:443 0.086 0.048 0.037 200 200 0 57 '
    '"GET https://lentago.dev:443/pricing HTTP/2.0" "Mozilla/5.0 (X11; Linux x86_64)" '
    'ECDHE-RSA-AES128-GCM-SHA256 TLSv1.2 '
    'arn:aws:elasticloadbalancing:us-east-2:123456789012:targetgroup/my-targets/73e2d6bc24d8a067 '
    '"Root=1-58337281-1d84f3d73c47ec4e58577259" "lentago.dev" '
    '"arn:aws:acm:us-east-2:123456789012:certificate/12345678-1234-1234-1234-123456789012" '
    '1 2018-07-02T22:22:48.364000Z "authenticate,forward" "-" "-" "10.0.0.1:443" "200" "-" "-"'
)

# user_agent containing an escaped double-quote — ALB writes \" inside quotes.
ESCAPED_UA_LINE = (
    'https 2026-07-04T10:00:00.000000Z app/solidago-alb/abc123 '
    '198.51.100.7:52000 10.0.2.5:443 0.001 0.002 0.000 404 404 120 512 '
    '"GET https://icecreamtofightwith.com:443/flavours HTTP/1.1" '
    '"Bot/1.0 (\\"weird\\" agent)" ECDHE-RSA-AES128-GCM-SHA256 TLSv1.3 '
    'arn:aws:elasticloadbalancing:us-east-2:123456789012:targetgroup/site/deadbeef '
    '"Root=1-abc" "icecreamtofightwith.com" "arn:aws:acm:us-east-2:1:certificate/x" '
    '0 2026-07-04T09:59:59.999000Z "forward" "-" "-" "10.0.2.5:443" "404" "-" "-"'
)

# Dropped request: no target chosen (target is -), request line is "- - -",
# elb_status_code 460, and the newer conn_trace_id field is present.
DROPPED_REQUEST_LINE = (
    'https 2026-07-04T10:01:00.000000Z app/solidago-alb/abc123 '
    '192.0.2.44:1000 - -1 -1 -1 460 - 0 0 '
    '"- - -" "-" - - '
    '"-" "Root=1-def" "-" "-" '
    '-1 2026-07-04T10:00:59.900000Z "-" "-" "-" "-" "-" "-" "-" '
    'TID_1234567890abcdef'
)

ALL_LINES = [HTTP_LINE, HTTPS_LINE, ESCAPED_UA_LINE, DROPPED_REQUEST_LINE]
