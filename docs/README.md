# Developer Documentation

Practical docs for working **inside** the Second Brain codebase. The formal design
rationale (use cases, UML) lives in `Second-Brain-Design-Document.docx`; these files are
the day-to-day reference for writing code.

| Doc | Read it when you want to… |
|-----|---------------------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | understand how the layers fit together and how a message flows through the system |
| [MODULES.md](MODULES.md) | look up what a specific module/class is responsible for and its status |
| [DATA_MODEL.md](DATA_MODEL.md) | know the exact vault layout, frontmatter schemas, and `state_db` tables |
| [DEVELOPMENT.md](DEVELOPMENT.md) | set up locally, follow conventions, add a new tool, run tests, deploy |

**Golden rule of this codebase:** the Markdown vault is the single **source of truth**.
The embedding index is *derived* (rebuildable), and `state_db` holds only *operational*
state. If you ever have to choose where a piece of data lives, ask which of those three
it is.
