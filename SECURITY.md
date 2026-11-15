# Security

HerdR Layouts runs as your user with no sandbox. It talks to the local HerdR CLI and Unix socket, moves and resizes panes, and writes saved layouts to the plugin config directory. It does not read pane contents, send input to panes, or use the network.

Report vulnerabilities privately through GitHub security advisories on this repository (Security tab, "Report a vulnerability"). Please do not open a public issue for a vulnerability. Expect an acknowledgement within a few days.
