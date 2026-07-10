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

# Real 2026 production line: 34 fields. AWS appends new trailing fields to the
# ALB format over time -- this line carries conn_trace_id (field 30) plus four
# more fields the schema does not name (three "-" and a trailing IP). The parser
# must tolerate the surplus, not raise, and still extract the leading
# visitor-source fields. Captured shape from solidago-dev-alb.
EXTENDED_TRAILING_LINE = (
    'h2 2026-07-09T23:00:00.000000Z app/solidago-dev-alb/9cd2662c6b66898b '
    '203.0.113.55:44321 10.0.10.4:8080 0.001 0.010 0.000 200 200 42 1024 '
    '"GET https://lentago.dev:443/ HTTP/2.0" '
    '"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" '
    'TLS_AES_128_GCM_SHA256 TLSv1.3 '
    'arn:aws:elasticloadbalancing:us-east-1:365184644049:targetgroup/solidago-dev-app-tg/13adb34fe2cf989a '
    '"Root=1-6a5054b3-030f0db75e12c4425817824e" "lentago.dev" '
    '"arn:aws:acm:us-east-1:365184644049:certificate/460041fa-52e7-4240-96ee-d1a4b19ad7bf" '
    '100 2026-07-09T22:59:59.900000Z "forward" "-" "-" "10.0.10.4:8080" "200" "-" "-" '
    'TID_bd7b1888dbb28d409aff3ec7256f89f9 "-" "-" "-" 100.29.136.126'
)

ALL_LINES = [
    HTTP_LINE,
    HTTPS_LINE,
    ESCAPED_UA_LINE,
    DROPPED_REQUEST_LINE,
    EXTENDED_TRAILING_LINE,
]
