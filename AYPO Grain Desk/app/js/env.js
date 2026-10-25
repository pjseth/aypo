/* Build-time environment, for the one thing the app cannot read at runtime: an API key.

   This file is a stub and it is listed in .gitignore, so whatever `tools/make_env.py` writes
   over it locally can never be committed. Run

       ANTHROPIC_API_KEY=sk-ant-... py -3.11 tools/make_env.py

   to bake the key in, or paste one into the app and it is kept in this browser's
   localStorage instead. Either way the desk works without one: every generated document has
   a deterministic template behind it, and that template is what the demo runs on. */
window.GD_ENV = { ANTHROPIC_API_KEY: "" };
