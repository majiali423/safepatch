# tornado-10

WebSocket handlers finish the HTTP upgrade early, but must retain request-handler
state until the WebSocket actually closes. A handler should still be able to
call `render_string` from `on_message` after the connection is established.

The upstream reference fix changes two product files: the generic request
handler exposes cycle cleanup separately, and the WebSocket handler defers it
until close. The reference patch is never exposed to the Agent.
