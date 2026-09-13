# EPUB import

Import a DRM-free EPUB using the existing **Import** button. EPUB 2 and EPUB 3
books are read in their spine order. Navigation labels and HTML headings become
editable Markdown headings, which the existing M4B exporter uses as chapters.
Review the imported text and headings before generating audio: unusual layouts,
footnotes and deeply nested navigation may need manual editing. Image-only books
and DRM-protected text are not supported.

When available, the importer reads the book title, authors, language, publisher,
description, publication date, copyright and explicitly identified ISBN. The book
title remains separate from the project's name. The cover is copied into the
project's assets, so exporting does not depend on a temporary image or the original
EPUB remaining available. Book metadata can still be edited using the existing UI.

Choose **M4B** for an audiobook with metadata, cover and chapter markers. Playback
and chapter presentation depend on the player. Other audio formats retain their
existing export behavior.

Two optional settings are available beside the output folder:

- **Save audio next to the imported document** uses the source folder while the
  source file exists; otherwise the configured output folder is used.
- **Use the imported filename as the audio base name** uses the source name,
  preserving the existing `_voice` / `_mix` suffixes and collision numbering.

Both settings are disabled by default. They also work with imported TXT, Markdown
and DOCX documents. Neither option renames the project. Starting a new project
clears the imported source reference; importing another book replaces the previous
book's extracted metadata. Plain-text imports preserve manually configured book
properties unless they came from the preceding EPUB import.

This implementation adapts the idea contributed by
[Tomas-Falcon in PR #23](https://github.com/estebanstifli/LocalText2Voice/pull/23)
to the current project storage and audio pipeline. It uses Python's standard
ZIP, XML and HTML readers, with no additional package dependencies.
