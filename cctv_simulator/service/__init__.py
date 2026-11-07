"""Local core service: the optic engine behind a versioned JSON-RPC 2.0 API.

The desktop shell (Tauri + WebView2) starts ``python -m cctv_simulator.service``
as a sidecar process and talks to it over stdin/stdout, one JSON object per
line. The service owns no UI; every number the shell shows comes from here
(Rule 1). Kept import-light on purpose (Rule 7): nothing heavy is imported
until a method that needs it is called.
"""

API_VERSION = "1.0"
