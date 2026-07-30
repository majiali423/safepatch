# thefuck-16

The generated Bash and Zsh aliases must declare `TF_ALIAS`,
`PYTHONIOENCODING`, and `TF_SHELL_ALIASES` inside the alias command substitution.
Declaring them outside the alias leaks state and breaks the command at execution
time.

The upstream reference fix spans four product files, with the core behavior
implemented consistently in the Bash and Zsh shell adapters. The reference
patch is never exposed to the Agent.
