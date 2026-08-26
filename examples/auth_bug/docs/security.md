# Security requirements

## Session handling

- Sessions must be invalidated after a period of user inactivity.
- Session timeout: 30 minutes.
- Logging out must immediately end the session server-side, not just clear
  the client cookie.

## Authentication

- Login must reject unknown or malformed session cookies.
- Passwords are never logged or stored in plaintext.
