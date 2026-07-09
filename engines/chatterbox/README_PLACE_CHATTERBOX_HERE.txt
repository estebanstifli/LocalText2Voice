Chatterbox no longer needs a bundled engine executable in this folder.

LocalText2Voice installs the optional Chatterbox Python dependencies into the
embedded Python runtime on demand, then launches a persistent worker process
during generation. Model assets are cached in the user's local app data folder.
